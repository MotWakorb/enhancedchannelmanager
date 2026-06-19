"""Phase-2 DBAS restore importers.

Each module here restores one entity category from a Dispatcharr export archive,
emitting per-entity results into the shared restore contracts
(``dbas.restore_contracts``): an :class:`~dbas.restore_contracts.EntityCategoryReport`
inside a :class:`~dbas.restore_contracts.RestoreReport`, destination-id
registrations in an :class:`~dbas.restore_contracts.IdRemapTable`, and
created-entity records in a :class:`~dbas.restore_contracts.RollbackLedger` so a
later failure can compensate-delete.

The first importer to land here is ``users`` — the crown-jewel
``dispatcharr_users`` category (bead ``enhancedchannelmanager-l1p4p``), the most
security-sensitive of the Phase-2 importers (privilege-escalation surface).
"""
