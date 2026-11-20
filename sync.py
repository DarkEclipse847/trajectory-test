from collections import defaultdict
from itertools import islice
from typing import Iterable, Iterator

from sqlalchemy import select

from models import AbstractCustomer

FIELDS = ("amo_customers", "amo_leads", "amo_companies", "amo_contacts")


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _general(obj):
    return {
        "id": _get(obj, "id"),
        "login": _field(obj, "Логин"),
        "name": _get(obj, "name") or "",
    }


def _field(obj, name):
    values = _get(obj, "custom_fields_values", {}) or {}
    if isinstance(values, dict):
        value = values.get(name)
        return value.get("value") if isinstance(value, dict) else value
    for item in values:
        if item.get("field_name") == name:
            vals = item.get("values") or []
            return vals[0].get("value") if vals else None
    return None


def _entity(obj):
    return {"general": _general(obj), "api_value": obj.as_dict() if hasattr(obj, "as_dict") else obj}


def _embedded(obj, key):
    values = _get(obj, key, []) or []
    return [_entity(v) if not (isinstance(v, dict) and "general" in v) else v for v in values]


def _batches(items: Iterable, size: int) -> Iterator[list]:
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


def _unique_login(index, login):
    matches = {row.id: row for row in index.get(login, ())}
    return next(iter(matches.values())) if len(matches) == 1 else None


def _merge_entity_lists(target, source):
    for key in FIELDS:
        by_id = {x.get("general", {}).get("id"): x for x in getattr(target, key) or []}
        for item in getattr(source, key) or []:
            by_id.setdefault(item.get("general", {}).get("id"), item)
        setattr(target, key, list(by_id.values()))


def collect_leads_customers(session, customers: Iterable, leads: Iterable,
                            is_full=True, batch_size=500):
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    try:
        rows = session.execute(select(AbstractCustomer)).scalars().all()
        by_crm_customer, by_crm_lead = {}, {}
        customer_login = defaultdict(list)
        lead_login = defaultdict(list)
        for existing in rows:
            for item in existing.amo_customers or []:
                by_crm_customer[item.get("general", {}).get("id")] = existing
                login = item.get("general", {}).get("login")
                if login:
                    customer_login[login].append(existing)
            for item in existing.amo_leads or []:
                by_crm_lead[item.get("general", {}).get("id")] = existing
                login = item.get("general", {}).get("login")
                if login:
                    lead_login[login].append(existing)
        touched = set()
        seen_customer_ids, seen_lead_ids = set(), set()
        seen_contact_ids, seen_company_ids = set(), set()

        for batch in _batches(customers, batch_size):
            for obj in batch:
                if not is_full and _get(obj, "status_id") == 111349:
                    continue
                login = _field(obj, "Логин")
                if not login:
                    continue
                entity_id = _get(obj, "id")
                row = by_crm_customer.get(entity_id)
                if row is None:
                    row = _unique_login(lead_login, login)
                payload = _entity(obj)
                if row is None:
                    row = AbstractCustomer(amo_customers=[payload], amo_leads=[], amo_contacts=[], amo_companies=[])
                    session.add(row)
                else:
                    row.amo_customers = _upsert(row.amo_customers, payload)
                contacts, companies = _embedded(obj, "contacts"), _embedded(obj, "companies")
                row.amo_contacts = _upsert_many(row.amo_contacts, contacts)
                row.amo_companies = _upsert_many(row.amo_companies, companies)
                seen_contact_ids.update(x.get("general", {}).get("id") for x in contacts)
                seen_company_ids.update(x.get("general", {}).get("id") for x in companies)
                by_crm_customer[entity_id] = row
                customer_login[login] = [row]
                touched.add(row)
                seen_customer_ids.add(entity_id)

            session.flush()

        for batch in _batches(leads, batch_size):
            for obj in batch:
                if not is_full and _get(obj, "status_id") == 143:
                    continue
                entity_id, login = _get(obj, "id"), _field(obj, "Логин")
                row = by_crm_lead.get(entity_id)
                login_row = _unique_login(customer_login, login) if login else None
                if row is None:
                    row = login_row
                elif login_row is not None and login_row.id != row.id:
                    _merge_entity_lists(row, login_row)
                    session.delete(login_row)
                    touched.discard(login_row)
                payload = _entity(obj)
                if row is None:
                    row = AbstractCustomer(amo_customers=[], amo_leads=[payload], amo_contacts=[], amo_companies=[])
                    session.add(row)
                else:
                    row.amo_leads = _upsert(row.amo_leads, payload)
                contacts = _embedded(obj, "contacts")
                row.amo_contacts = _upsert_many(row.amo_contacts, contacts)
                seen_contact_ids.update(x.get("general", {}).get("id") for x in contacts)
                by_crm_lead[entity_id] = row
                customer_login[login] = [row] if login else []
                lead_login[login] = [row] if login else []
                touched.add(row)
                seen_lead_ids.add(entity_id)

            session.flush()
        touched_ids = {row.id for row in touched}
        if is_full:
            for row in list(session.execute(select(AbstractCustomer)).scalars()):
                if row.id not in touched_ids:
                    session.delete(row)
                    continue
                row.amo_customers = _retain(row.amo_customers, seen_customer_ids)
                row.amo_leads = _retain(row.amo_leads, seen_lead_ids)
                row.amo_contacts = _retain(row.amo_contacts, seen_contact_ids)
                row.amo_companies = _retain(row.amo_companies, seen_company_ids)
        session.commit()
    except Exception:
        session.rollback()
        raise


def _upsert(items, item):
    items = list(items or [])
    entity_id = item.get("general", {}).get("id")
    for i, old in enumerate(items):
        if old.get("general", {}).get("id") == entity_id:
            items[i] = item
            return items
    items.append(item)
    return items


def _upsert_many(items, incoming):
    for item in incoming:
        items = _upsert(items, item)
    return items


def _retain(items, ids):
    return [item for item in (items or [])
            if item.get("general", {}).get("id") in ids
            and "удалить" not in (item.get("general", {}).get("name") or "").lower()]
