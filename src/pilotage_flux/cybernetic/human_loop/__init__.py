"""V12.4 — Workflow humain complet (rôles, audit, escalation, notifications).

Cette couche complète V12.3 (Delta engine) en ajoutant :

  - **Rôles & permissions** : qui peut approuver quoi
      operator   → peut approuver L3
      supervisor → peut approuver L3 + L4
      admin      → permissions complètes + config

  - **Audit trail** : log immuable de toutes les transitions
      (submit, approve, reject, escalate, role_change)

  - **Escalation** : un L3 en attente > seuil_min est escaladé
    automatiquement à L4 (et notifié au supervisor)

  - **Notifications** : queue in-memory de notifications dispatch
    par utilisateur destinataire

  - **Dashboard** : aggrégations queue + audit pour visibilité opé

Workflow type :

  V12.3.dispatch → submit_to_approval_queue
    → V12.4.audit_log.log_event('submitted', ...)
    → V12.4.notifications.notify(target_role, message)
    → (timeout) V12.4.escalation.escalate_overdue
    → user actionne via CLI ou API
    → V12.4.audit_log.log_event('approved'/'rejected', ...)
"""

from pilotage_flux.cybernetic.human_loop.roles import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    ROLES,
    can_approve,
    get_user_role,
    set_user_role,
)
from pilotage_flux.cybernetic.human_loop.audit_log import (
    AuditEvent,
    list_audit_events,
    log_audit_event,
)
from pilotage_flux.cybernetic.human_loop.escalation import (
    EscalationResult,
    detect_overdue,
    escalate_overdue,
)
from pilotage_flux.cybernetic.human_loop.notifications import (
    Notification,
    list_notifications,
    notify,
)
from pilotage_flux.cybernetic.human_loop.dashboard import (
    DashboardSnapshot,
    snapshot_dashboard,
)

__all__ = [
    "ROLE_OPERATOR",
    "ROLE_SUPERVISOR",
    "ROLE_ADMIN",
    "ROLES",
    "can_approve",
    "get_user_role",
    "set_user_role",
    "AuditEvent",
    "log_audit_event",
    "list_audit_events",
    "Notification",
    "notify",
    "list_notifications",
    "EscalationResult",
    "detect_overdue",
    "escalate_overdue",
    "DashboardSnapshot",
    "snapshot_dashboard",
]
