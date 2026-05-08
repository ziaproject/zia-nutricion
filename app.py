from whatsapp import app

# Web API routes
from clients.zia_nutricion.app_web import register_routes
register_routes(app)
