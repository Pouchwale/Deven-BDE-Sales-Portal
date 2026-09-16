"""Importing this package registers every mapper.

SQLAlchemy resolves relationship() strings lazily against a shared registry,
so a model that is never imported makes an unrelated model fail to configure.
Importing them all in one place removes that class of error.
"""
from app.db.base import Base
from app.models.chat import ChatConversation, ChatMessage, ChatToolCall
from app.models.customer import Customer, CustomerActivity, InvoiceLine, SapImport
from app.models.feedback import (
    Feedback,
    FeedbackAlert,
    FeedbackDepartmentRating,
    FeedbackImport,
    FeedbackRequest,
    FeedbackSyncEvent,
)
from app.models.lead import Lead, LeadActivity
from app.models.post_sale import PostSaleRecord
from app.models.org import Department, Team, User
from app.models.reference import CustomerReference
from app.models.system import AppSetting, AuditEvent, Notification

__all__ = [
    "AppSetting",
    "AuditEvent",
    "Base",
    "ChatConversation",
    "ChatMessage",
    "ChatToolCall",
    "Customer",
    "CustomerActivity",
    "CustomerReference",
    "Department",
    "Feedback",
    "FeedbackAlert",
    "FeedbackDepartmentRating",
    "FeedbackImport",
    "FeedbackRequest",
    "FeedbackSyncEvent",
    "InvoiceLine",
    "Lead",
    "LeadActivity",
    "Notification",
    "SapImport",
    "Team",
    "User",
]
