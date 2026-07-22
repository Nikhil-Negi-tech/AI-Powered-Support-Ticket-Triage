# pipeline_config.py  (copy this file, rename it, fill in real values, keep out of git)

AZURE_DEVOPS_CONFIG = {
    "org": "your-ado-org",
    "project": "your-project",
    "pat": "PASTE_PERSONAL_ACCESS_TOKEN_HERE",   # scope: Work Items (Read)
    "work_item_type": "Hardware Request",         # match your ADO work item type name
    "area_path": None,                            # optional narrowing, e.g. "Project\\IT\\Requests"
}

# App registration in Entra ID with Microsoft Graph "Sites.ReadWrite.All" (application permission),
# admin-consented. site_id/list_id: see the two helper snippets below to fetch them once.
SHAREPOINT_CONFIG = {
    "tenant_id": "PASTE_TENANT_ID",
    "client_id": "PASTE_APP_CLIENT_ID",
    "client_secret": "PASTE_APP_CLIENT_SECRET",
    "site_id": "PASTE_SITE_ID",
    "list_id": "PASTE_LIST_ID",
}

# ---- one-time helper to find site_id / list_id ----
# import requests, msal
# token = ...  # use get_graph_token() from either script
# GET https://graph.microsoft.com/v1.0/sites/{hostname}:/sites/{site-path}
#     -> gives you site_id
# GET https://graph.microsoft.com/v1.0/sites/{site_id}/lists
#     -> gives you list_id for "HardwareRequests"
