# ZIA Platform — Guía Completa del SaaS

## Arquitectura en una frase
**Un motor. Un JSON por cliente. Para cambiar de cliente: 30 minutos.**

---

## Estructura de carpetas

```
zia-saas/
│
├── core/
│   └── engine.py          ← Motor central. NUNCA se toca.
│
├── clients/
│   ├── zia-nutricion/
│   │   └── config.json    ← Config del B2C base
│   ├── herbolario-navarro/
│   │   └── config.json    ← Config de Herbolario Navarro
│   ├── fitlife-gym/
│   │   └── config.json    ← Config de FitLife Gym
│   └── [nuevo-cliente]/
│       └── config.json    ← Tu próximo cliente
│
└── platform/
    └── app.py             ← Servidor Flask multi-cliente
```

---

## ¿Cómo pivotar de un cliente a otro?

### Opción A — Cambiar el cliente activo en Railway
```bash
# En Railway → Variables de entorno
CLIENT_ID=herbolario-navarro   # Activa Herbolario Navarro
CLIENT_ID=fitlife-gym          # Activa FitLife Gym
CLIENT_ID=zia-nutricion        # Activa el B2C base
```
**Tiempo: 30 segundos. Zero código.**

### Opción B — Varios clientes en producción simultánea
Despliega un servicio Railway por cliente, cada uno con su `CLIENT_ID`.

```
Railway Service 1: CLIENT_ID=zia-nutricion        → zianutricion.com
Railway Service 2: CLIENT_ID=herbolario-navarro   → hn.zianutricion.com
Railway Service 3: CLIENT_ID=fitlife-gym          → fitlife.zianutricion.com
```

---

## ¿Cómo crear un nuevo cliente en 30 minutos?

### Paso 1 — Duplica la carpeta de config (2 min)
```bash
cp -r clients/zia-nutricion clients/nuevo-supermercado
```

### Paso 2 — Edita el config.json (15 min)
Los campos mínimos obligatorios:
```json
{
  "_meta": { "client_id": "nuevo-supermercado", "type": "B2B" },
  "branding": {
    "company_name": "Nombre del Supermercado",
    "primary_color": "#COLOR_DE_MARCA",
    "logo_url": "https://...",
    "whatsapp_number": "+34XXXXXXXXX"
  },
  "bot": {
    "name": "ZIA",
    "welcome_message": "¡Hola! Soy ZIA de [Nombre]...",
    "personality": "Descripción del tono y personalidad",
    "mission": "Qué hace ZIA para este cliente"
  },
  "catalog": {
    "categories": [
      {
        "name": "Categoría 1",
        "products": [
          {"name": "Producto", "price": "X,XX€", "unit": "Xg"}
        ]
      }
    ]
  }
}
```

### Paso 3 — Despliega en Railway (5 min)
```bash
# En Railway, añade variable de entorno:
CLIENT_ID=nuevo-supermercado
# Redeploy automático
```

### Paso 4 — Conecta WhatsApp (5 min)
- Apunta el webhook de Twilio al nuevo endpoint
- Listo

### Paso 5 — Test (3 min)
- Envía "Hola" al número de WhatsApp
- Verifica el flujo completo

---

## El config.json — Campos explicados

### `_meta` — Metadatos
```json
{
  "client_id": "ID único sin espacios",
  "type": "B2C | B2B",
  "sector": "herbolario | supermercado | gimnasio | clinica | empresa",
  "version": "1.0"
}
```

### `branding` — Identidad visual
```json
{
  "company_name": "Nombre visible en el bot",
  "primary_color": "#HEX del color de marca",
  "logo_url": "URL del logo",
  "whatsapp_number": "Número de WhatsApp del cliente"
}
```

### `bot` — Personalidad y comportamiento
```json
{
  "name": "Nombre del bot (normalmente ZIA)",
  "personality": "Descripción detallada del tono y estilo",
  "mission": "Qué hace y para qué existe",
  "welcome_message": "Primer mensaje que ve el usuario",
  "tone": "Resumen del tono en pocas palabras"
}
```

### `ai` — Configuración de la IA
```json
{
  "model": "gpt-4o-mini | gpt-4o",
  "max_tokens": 1500,
  "temperature": 0.7,
  "extra_context": "Instrucciones adicionales específicas de este cliente"
}
```
**Ajusta `temperature`:**
- 0.5-0.6 → Respuestas más precisas y consistentes (clínicas, médico)
- 0.7-0.8 → Equilibrio (estándar)
- 0.8-0.9 → Más creativo (recetas, planes variados)

### `catalog` — Productos del cliente
```json
{
  "type": "herbolario | supermercado | gimnasio | general",
  "categories": [
    {
      "name": "Nombre de la categoría",
      "products": [
        {
          "name": "Nombre del producto",
          "price": "X,XX€",
          "unit": "Xg / Xml / X uds",
          "sku": "CODIGO_INTERNO",
          "url": "URL directa al producto",
          "bestseller": true
        }
      ]
    }
  ]
}
```

### `integrations` — APIs y conexiones
```json
{
  "cart": {
    "enabled": true,
    "type": "url_template | api_rest | shopify | woocommerce | magento",
    "base_url": "URL base del carrito",
    "search_url": "URL de búsqueda con {product} como placeholder",
    "add_to_cart_url": "URL para añadir con {sku} y {qty}"
  },
  "supermarkets": {
    "enabled": true,
    "compare": true,
    "providers": [...]
  }
}
```

---

## Niveles de integración de carrito

### Nivel 1 — URL de búsqueda (disponible hoy)
ZIA genera la lista y para cada producto crea un link de búsqueda.
```json
"search_url": "https://tienda.cliente.es/buscar?q={product}"
```

### Nivel 2 — Añadir al carrito por URL (requiere acuerdo comercial)
```json
"add_to_cart_url": "https://tienda.cliente.es/cart/add?sku={sku}&qty={qty}"
```

### Nivel 3 — API oficial (requiere API del cliente)
```json
"type": "api_rest",
"api_endpoint": "https://api.cliente.es/v1/cart",
"api_key_env": "CLIENTE_API_KEY"
```

### Nivel 4 — Plataformas estándar (futuro)
```json
"type": "shopify | woocommerce | magento | prestashop"
```

---

## Variables de entorno (.env)

```bash
# OBLIGATORIAS
OPENAI_API_KEY=sk-...
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
CLIENT_ID=zia-nutricion

# OPCIONALES (según cliente)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJ...
STRIPE_SECRET_KEY=sk_live_...
PORT=5000
```

---

## Costes estimados por cliente/mes

| Usuarios activos | OpenAI | Twilio | Railway | Total |
|-----------------|--------|--------|---------|-------|
| 50              | ~30€   | ~8€    | 5€      | ~43€  |
| 200             | ~120€  | ~30€   | 5€      | ~155€ |
| 1.000           | ~600€  | ~150€  | 20€     | ~770€ |

**Margen bruto B2B:** ~80% con licencia mensual de 199-499€

---

## Roadmap técnico

### Ya disponible
- [x] Motor multi-cliente con JSON de configuración
- [x] Integración WhatsApp via Twilio
- [x] Catálogo de productos por cliente
- [x] Links de búsqueda en catálogos
- [x] Gestión de planes (free/individual/pro)

### Próximas semanas
- [ ] Supabase — base de datos de usuarios
- [ ] Stripe — pagos y suscripciones automáticas
- [ ] Panel de control por cliente con métricas

### Próximos meses
- [ ] API de carrito nivel 2 (Herbolario Navarro, Carrefour)
- [ ] Integración Shopify/WooCommerce
- [ ] Panel de administración web para gestionar clientes
- [ ] Subdominio automático por cliente (cliente.zianutricion.com)
- [ ] Multi-idioma (PT, FR, IT)
