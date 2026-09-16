"""Default outgoing message templates.

The point of these is that nobody on the team retypes "could you leave us a
review, here is the link" thirty times a week. They are seeded into
`app_settings`, so an admin can reword them in Settings without a deploy —
these are only the starting text.

Placeholders available in every template:

    {customer_name}   the account name as SAP spells it
    {sap_code}        e.g. C2080
    {sender_name}     the portal user sending the request
    {our_company}     from the company.name setting
    {link}            the feedback form or Google review URL

Substitution is by allow-list in app/services/messaging.py — never
str.format on an admin-edited string, which would turn a stray brace into a
crash and `{0.__class__}` into an information leak.
"""
from __future__ import annotations

FEEDBACK_WHATSAPP = (
    "Hello {customer_name}, this is {sender_name} from {our_company}.\n\n"
    "Thank you for your recent order. Could you spare a minute to tell us how "
    "we did? It genuinely helps us improve.\n\n"
    "{link}\n\n"
    "Thank you!"
)

FEEDBACK_EMAIL_SUBJECT = "How did we do, {customer_name}?"

FEEDBACK_EMAIL_BODY = (
    "Dear {customer_name},\n\n"
    "Thank you for your recent order with {our_company}.\n\n"
    "We would value your feedback on how we did. The form takes less than a "
    "minute:\n\n"
    "{link}\n\n"
    "Thank you for your time.\n\n"
    "Best regards,\n"
    "{sender_name}\n"
    "{our_company}"
)
