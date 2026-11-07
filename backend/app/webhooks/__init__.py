"""Vapi webhook ingress: authentication, redaction, call binding, dedup and handler registries.

Kept import-free on purpose: ``app.secrets_registry`` imports ``app.webhooks.redact``, and the
router (``app.webhooks.vapi``) imports the registry back.
"""
