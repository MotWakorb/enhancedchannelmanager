"""
Authentication providers package.

Contains clients for external authentication providers:
- Dispatcharr: SSO with Dispatcharr instance
- SAML: SAML 2.0 (future)
- LDAP: LDAP/Active Directory (future)
"""
from .dispatcharr import DispatcharrClient, DispatcharrAuthResult

__all__ = ["DispatcharrClient", "DispatcharrAuthResult"]
