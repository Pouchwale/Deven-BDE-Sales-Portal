"""Every router, mounted under /api."""
from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    post_sale,
    admin,
    auth,
    chat,
    customers,
    dashboard,
    feedback,
    feedback_sync,
    leads,
    notifications,
    references,
    timeline,
    users,
    work_queue,
)

api_router = APIRouter(prefix="/api")

# Auth and people
api_router.include_router(auth.router)
api_router.include_router(users.router)

# Module 1 - reference tracking.
# The timeline router is mounted FIRST: it owns /customers/review-link, which
# /customers/{customer_id} would otherwise match as an id.
api_router.include_router(timeline.router)
api_router.include_router(customers.router)
api_router.include_router(references.router)

# Module 2 - assigned leads and the two-bucket work queue
api_router.include_router(leads.router)
api_router.include_router(work_queue.router)

# Module 3 - customer feedback
api_router.include_router(feedback.router)
# Mounted after: /feedback/sync/* must not be caught by a
# /feedback/{something} route.
api_router.include_router(feedback_sync.router)

# The post-sale sheet: what the business knows about a won lead after the
# sale. Administrators only - see app/api/post_sale.py.
api_router.include_router(post_sale.router)

# Cross-cutting
api_router.include_router(notifications.router)
api_router.include_router(dashboard.router)
api_router.include_router(chat.router)
api_router.include_router(admin.router)
