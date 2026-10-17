from fastapi import APIRouter

from app.interfaces.endpoints.knowbot_data_routes import router as knowbot_data_router
from app.interfaces.endpoints.knowbot_routes import admin_router as knowbot_admin_router
from app.interfaces.endpoints.knowbot_routes import web_router as knowbot_web_router
from app.interfaces.http.admin.auth import router as admin_auth_router
from app.interfaces.http.admin.diagnostics import router as admin_diagnostics_router
from app.interfaces.http.internal.delivery_metrics import (
    router as delivery_metrics_router,
)
from app.interfaces.http.web.auth import router as web_auth_router
from app.interfaces.http.web.cross_app import router as web_cross_app_router
from app.interfaces.http.web.interactions import router as web_interactions_router

router = APIRouter()
router.include_router(admin_auth_router)
router.include_router(web_auth_router)
router.include_router(admin_diagnostics_router)
router.include_router(delivery_metrics_router)
router.include_router(web_interactions_router)
router.include_router(web_cross_app_router)
router.include_router(knowbot_web_router)
router.include_router(knowbot_admin_router)
router.include_router(knowbot_data_router)
