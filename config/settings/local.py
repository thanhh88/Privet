from .base import *

INSTALLED_APPS += [
    "UI.apps.UIConfig",
]

PRIVET_LOGIN_URL = "/accounts/login/"
PRIVET_REGISTER_URL = "/accounts/register/"

DEBUG = True