from whatsapp import app

# Web API routes
from clients.zia_nutricion.app_web import app as web_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {'/web': web_app})
