from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import BigInteger, DateTime, func, Index
from sqlalchemy.dialects.postgresql import JSONB

db = SQLAlchemy()


class AbstractCustomer(db.Model):
    __tablename__ = "abstract_customer"
    id = db.Column(BigInteger, primary_key=True)
    amo_customers = db.Column(JSONB, nullable=False, default=list)
    amo_leads = db.Column(JSONB, nullable=False, default=list)
    amo_companies = db.Column(JSONB, nullable=False, default=list)
    amo_contacts = db.Column(JSONB, nullable=False, default=list)
    send_methods = db.Column(JSONB, nullable=False, default=list)
    created_at = db.Column(DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(DateTime(timezone=True), onupdate=func.now())


Index("ix_abstract_customer_customers_gin", AbstractCustomer.amo_customers, postgresql_using="gin")
Index("ix_abstract_customer_leads_gin", AbstractCustomer.amo_leads, postgresql_using="gin")
