# 📊 Monitor de Compras Ágiles (Mercado Público Chile)

Sistema que **una vez al día** extrae las **Compras Ágiles vigentes** publicadas en
[Mercado Público](https://www.mercadopublico.cl) usando la **API oficial de Compra Ágil v2**,
busca **precios de referencia en tiendas nacionales**, y calcula si es **rentable
adjudicarse cada compra** antes de cotizar. Los resultados se publican en un
**dashboard web** listo para compartir.

> ⚠️ **Para proveedores**: esta herramienta es una ayuda de decisión. La estimación
> usa precios públicos de referencia; verifique la ficha oficial de cada CA y sus
> condiciones (entrega, garantías, boleta) antes de cotizar.

---

## 🔄 ¿Qué hace?

```
 ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────┐
 │ 1. INGESTA       │   │ 2. PRECIOS       │   │ 3. RENTABILIDAD  │   │ 4. DASHBOARD │
 │ API Compra Ágil  │──▶│ Solotodo / ML /  │──▶│ costo neto + IVA │──▶│ web/index.html│
 │ v2: CAs con      │   │ tiendas JSON-LD  │   │ flete + margen + │   │ (autocontenido│
 │ estado publicada │   │ (plugable)       │   │ riesgo competencia│  │  y responsive)│
 └──────────────────┘   └──────────────────┘   └──────────────────┘   └──────────────┘
```

Por cada Compra Ágil vigente entrega:

- **Veredicto**: `RENTABLE` / `MARGINAL` / `NO RENTABLE` / `SIN DATOS`.
- **Margen y utilidad estimada** con la estructura de costos completa.
- **Precio estimado por producto** (neto) con las fuentes usadas y sus enlaces.
- **Advertencia de competencia**: si ya existe cotización de otro proveedor bajo tu costo.
- **Fechas**: publicación y cierre del llamado vigente.

---

## 🚀 Inicio rápido

```bash
# 1) Clonar e instalar
git clone https://github.com/TU_USUARIO/compras-agiles-monitor.git
cd compras-agiles-monitor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) Probar sin ticket (datos sintéticos marcados como DEMO)
python -m cam_monitor demo

# 3) Con datos reales: obtener ticket y correr
cp .env.example .env        # edite .env y pegue su MP_TICKET
python -m cam_monitor test  # verifica el ticket
python -m cam_monitor run   # corrida completa
```

Al terminar, abra **`web/index.html`** en su navegador. También puede servirlo:
`python -m http.server -d web 8000`.

### Obtener el ticket de la API (gratis)

1. Entre a <https://www.chilecompra.cl/api/> y presione **“Pide tu ticket”**.
2. Inicie sesión con **Clave Única** y acepte los términos.
3. El ticket llega **automáticamente por correo** (revise spam).
4. Defínalo como variable de entorno: `export MP_TICKET=TU_TICKET`
   (o guárdelo en `.env`, que no se sube al repositorio).

El ticket tiene **cuota diaria** de solicitudes (se reinicia cada día calendario).
Este proyecto la respeta: `max_detalles` limita los detalles consultados, la
caché evita repetir búsquedas y el **procesamiento incremental** no vuelve a
consultar las CAs ya evaluadas (ver ♻️ más abajo).

---

## 🧮 Cómo se calcula la rentabilidad

```
ingreso_neto      = monto_disponible de la CA (se asume neto; configurable)
costo_unitario    = precio_tienda / (1 + IVA)      # los precios de tiendas incluyen IVA
costo_adquisicion = Σ costo_unitario × cantidad    # por cada producto solicitado
costo_total       = costo_adquisicion + flete + comisión%
utilidad          = ingreso_neto − costo_total
margen            = utilidad / ingreso_neto
```

- **Estrategia de precio** (config): con N precios encontrados se toma
  `minimo`, `percentil_25`, `mediana` (default) o `promedio`.
- **Veredictos**: `RENTABLE` si margen ≥ `margen_rentable` (default 10%);
  `MARGINAL` si 0–10%; `NO RENTABLE` si negativo.
- **Cobertura**: si menos del 60% de los ítems tiene precio encontrado,
  la CA queda `SIN DATOS` (no se arriesga una conclusión sin información).
- **Riesgo competencia**: la API entrega las cotizaciones registradas
  (`valor_neto` de proveedores activos). Si la mejor ya está bajo el 90% de
  tu costo, se marca la CA con ⚠: ganarla exigiría cotizar aún más bajo.
- **No incluye** costos de su empresa (transporte dedicado, personal,
  garantías, facturación): ajústelos vía `flete_estimado_clp` y
  `costo_transaccion_pct`, o agréguelos al margen mínimo exigido.

> Nota IVA: en Compra Ágil las cotizaciones se registran como **valor neto +
> impuestos**; el monto disponible publicado se trata como neto por defecto.
> Si su caso difiere, cambie `monto_disponible_es_neto` / `precios_tienda_incluyen_iva`.

---

## ♻️ Procesamiento incremental (no revisar dos veces)

El monitor está pensado para correr **una vez al día** y **no repetir trabajo**:

1. El **listado** de CAs vigentes se descarga completo cada día (barato: pocas
   páginas, no consume la cuota de detalles).
2. Cada CA del listado se compara con `data/analisis_previos.json` (el store de
   evaluaciones):
   - **Ya evaluada** → no se vuelve a consultar: recicla su detalle de
     productos y sus resultados de precios almacenados; solo se recalcula la
     aritmética con el monto, cierre y ofertas **frescos del día**.
   - **Nueva** → se le consulta el detalle y se buscan sus precios (aquí se
     consume la cuota, solo por lo nuevo).
   - **Quedó `SIN DATOS`** → se reintenta en corridas siguientes
     (`reintentar_sin_datos`), porque con el tiempo aparecen más precios de
     referencia.
3. Las CAs **cerradas** se podan solas del store y cada corrida deja el store
   listo para la siguiente.

Beneficios prácticos:

- **La cuota se destina solo a lo nuevo**: con `max_detalles: 30`, el día 1
  cubre las 30 más grandes, el día 2 las siguientes 30, y así hasta cubrir
  todo el mercado; desde ahí, cada día solo entra lo publicado nuevo.
- **Recalibrar es gratis**: si ajusta parámetros de `rentabilidad` (margen
  mínimo, estrategia de precio, flete…), el cambio se aplica a todas las CAs
  en la próxima corrida, porque la aritmética se recalcula siempre con los
  precios ya almacenados.
- El dashboard sigue mostrando **todas** las vigentes de hoy; su pie indica
  cuántas fueron reutilizadas ("sin reconsultar precios").

| Ajuste | Efecto |
|---|---|
| `rentabilidad.reutilizar_analisis` | `false` desactiva la reutilización (analiza todo siempre) |
| `rentabilidad.reintentar_sin_datos` | `false` no reintenta las que quedaron SIN DATOS |
| `python -m cam_monitor run --reanalizar` | fuerza reevaluar **todas** las CAs, solo esa corrida |

> En GitHub Actions el store se commitea junto al dashboard
> (`data/analisis_previos.json`), así que la memoria de "ya evaluadas"
> persiste entre corridas diarias.

---

## 🏪 Fuentes de precios (plugables)

| Proveedor     | Tipo                | Estado por defecto | Notas |
|---------------|---------------------|--------------------|-------|
| `solotodo`    | API pública chilena | ✅ activado        | Agrega decenas de tiendas; fuerte en electrónica |
| `mercadolibre`| API oficial (token automático) | ✅ activado        | Requiere secrets `ML_CLIENT_ID` + `ML_CLIENT_SECRET` (app en developers.mercadolibre.cl); si faltan, se omite con aviso |
| `vtex` (easy, jumbo) | API pública VTEX (`{cuenta}.myvtex.com`) | ✅ activado | JSON estable sin scraping: Easy (ferretería/hogar) y Jumbo (aseo/oficina) |
| `jsonld`      | HTML con schema.org | ⛔ opcional        | Cualquier tienda configurable (Paris, Sodimac…) |

Para **agregar una tienda** con JSON-LD (muchas e-commerce chilenas emiten
`<script type="application/ld+json">` con Product+Offer):

```yaml
pricing:
  proveedores:
    jsonld:
      activado: true
      tiendas:
        - nombre: paris
          url_busqueda: "https://www.paris.cl/search?text={query}"
          dominio: "https://www.paris.cl"
```

Para una tienda con **API propia**, herede de `PriceProvider`
(`cam_monitor/pricing/base.py`), implemente `buscar(query)` y regístrela en
`cam_monitor/pricing/__init__.py` (diccionario `PROVEEDORES_CODIFICADOS`).

> **Uso responsable**: las tiendas pueden bloquear scrapers (anti-bot) o
> prohibirlo en sus términos. El motor trata cada tienda como *best-effort*:
> si falla, la corrida continúa con el resto y la CA queda con menos cobertura.
> Respete rate limits (la caché de 20 h ayuda) y los ToS de cada sitio.

---

## 📋 CAs con detalle en PDF escaneado (descarga automática)

Algunas CAs no traen productos en la API: el detalle está solo en un **PDF
adjunto** de la ficha (por ejemplo, un listado DIDECO escaneado). Como la API
no expone la URL de descarga de esos documentos (verificado: solo entrega
`documentos[].id/nombre`), el monitor los obtiene con un navegador headless
(Playwright, instalado best-effort por el workflow):

1. Tras la ingesta detecta las CAs sin productos pero con `documentos[]`.
2. Abre `buscador.mercadopublico.cl/ficha?code={codigo}`, espera el render
   del SPA y captura los PDF que la página carga o enlaza (máx. 2 por CA).
3. Descarga el PDF, aplica OCR (tesseract) y agrega las líneas como
   productos (máx. `pricing.max_items_por_ca`). El origen queda marcado como
   `detalle_fuente = "buscador"` con caché en `data/adjuntos/{codigo}.json`.
4. En la misma corrida esos ítems reciben precios y análisis.

Si CloudFront bloquea al runner o el navegador no está disponible, la corrida
sigue sin detalle para esa CA y el dashboard muestra el aviso.

### Verificar si Actions puede leer la ficha (2 minutos)

Corra el workflow **"Experimento — ¿puede Actions leer
buscador.mercadopublico.cl?"** (pestaña Actions → Run workflow). El log dirá
si el acceso es 200 (descarga automática funcionará) o 403 (CloudFront
también bloquea a los runners). Contexto: el bloqueo detectado desde
servidores externos es de CloudFront y depende de la región/IP de origen;
los runners de GitHub salen de IPs de EE.UU., así que puede que pasen.

### Plan B — PDF manual (si el experimento da 403)

1. Abra la ficha en su navegador y descargue el PDF de la sección **Adjuntos**.
2. En GitHub: **Add file → Upload files** y súbalo a la carpeta `adjuntos/`
   con el nombre exacto `{codigo}.pdf` (ej: `adjuntos/2436-1903-COT26.pdf`).
3. La siguiente corrida lo lee con OCR (`detalle_fuente = "pdf-ocr"`).

El OCR es *best-effort*: en escaneos de buena calidad recupera la gran mayoría
de las filas, pero puede omitir o confundir alguna cantidad. Revise las
cantidades antes de cotizar.

---

## ⏰ Automatización diaria

### Opción A — GitHub Actions (incluida)

`.github/workflows/monitor.yml` corre el monitor **todos los días 13:30 UTC**
(10:30 en Chile) y guarda el resultado en el repo.

Setup:

1. Suba el proyecto a su GitHub.
2. En el repo: **Settings → Secrets and variables → Actions → New secret**,
   nombre `MP_TICKET`, valor su ticket.
3. Activa el workflow (la primera corrida puede dispararse manualmente desde
   la pestaña *Actions* → *Run workflow*).
4. Opcional: publique `web/` con **GitHub Pages** para ver el dashboard online.

### Opción B — Cron local / servidor

```bash
# 10:30 todos los días
30 10 * * * /ruta/compras-agiles-monitor/scripts/run_daily.sh >> /var/log/cam.log 2>&1
```

---

## 📁 Estructura del proyecto

```
compras-agiles-monitor/
├── cam_monitor/
│   ├── mp_client.py       # cliente API Compra Ágil v2 (ticket, paginación, 429)
│   ├── ingest.py          # CAs vigentes + detalle (productos solicitados)
│   ├── pricing/           # motor de precios + proveedores (solotodo, ml, jsonld)
│   ├── profitability.py   # motor de rentabilidad (IVA, flete, margen, veredictos)
│   ├── storage.py         # corridas en data/runs/, historial, puntero latest
│   ├── dashboard.py       # genera web/index.html con datos embebidos
│   ├── demo.py            # dataset sintético + proveedor demo
│   └── cli.py             # comandos: test | run | demo | web
├── web/
│   ├── dashboard_template.html   # plantilla (marcador __DATA_PLACEHOLDER__)
│   └── index.html                # dashboard generado (autocontenido)
├── data/                  # corridas JSON + latest/historial + analisis_previos.json (store incremental)
├── scripts/run_daily.sh   # cron local
├── .github/workflows/monitor.yml
├── config.yaml            # toda la configuración (comentada)
└── .env.example           # plantilla de secrets locales
```

### Comandos

| Comando                        | Descripción |
|--------------------------------|-------------|
| `python -m cam_monitor test`   | Verifica el ticket y muestra CAs de muestra |
| `python -m cam_monitor run`    | Corrida completa real (ingesta → precios → rentabilidad → dashboard) |
| `python -m cam_monitor run --max-ca 10` | Limita los detalles consultados (cuota) |
| `python -m cam_monitor run --sin-cache` | Fuerza re-consultar precios |
| `python -m cam_monitor run --reanalizar` | Fuerza reevaluar todas las CAs (ignora el store incremental) |
| `python -m cam_monitor demo`   | Corrida completa con datos de demostración |
| `python -m cam_monitor web`    | Regenera el dashboard desde la última corrida |

---

## 📖 Interpretación del dashboard

- **KPIs superiores**: totales por veredicto, monto disponible y utilidad potencial.
- **Filtros**: texto (producto/organismo/código), veredicto, región, margen mínimo y orden.
- **Cada tarjeta**: código de la CA (copiable), organismo, región, ofertas recibidas,
  monto, margen, utilidad y countdown de cierre. Al abrir el detalle verá el
  desglose por producto con las fuentes de precios y sus enlaces.
- **⚠ riesgo competencia**: la CA ya tiene cotizaciones bajo tu costo estimado.

---

## ⚠️ Limitaciones y buenas prácticas

1. **Los precios de referencia son de retail** (precio al público, IVA incluido).
   Su costo real como proveedor puede ser menor (mayorista) o mayor (garantías,
   facturación). Ajuste `estrategia` y agregue su margen exigido en `margen_rentable`.
2. **Cuota diaria de la API**: cada detalle consume 1 solicitud. Con cuotas
   bajas, baje `max_detalles` y `max_paginas`.
3. **Tiendas con anti-bot**: Falabella/Sodimac/Paris pueden responder 403 desde
   datacenters (incluido GitHub Actions). El proveedor `jsonld` es configurable
   y las corridas continúan aunque una tienda falle.
4. **Datos públicos**: la API es oficial y para uso responsable (no descargas
   masivas; ChileCompra recomienda ventanas nocturnas para cargas pesadas).
5. **No es asesoría comercial**: la decisión de cotizar (y a qué precio) es suya.

---

## 🗺️ Roadmap sugerido ("vamos de a poco")

- [x] **Fase 1** — Ingesta API oficial + motor de rentabilidad + dashboard demo
- [ ] **Fase 2** — Conectar su `MP_TICKET`, validar contra CAs reales y calibrar umbrales
- [ ] **Fase 3** — Agregar tiendas JSON-LD/APIs (Falabella, Sodimac, Hites…) según rubro
- [ ] **Fase 4** — Notificaciones (correo/Telegram) cuando aparece una CA rentable nueva
- [ ] **Fase 5** — Histórico comparativo (data/historial.json ya se guarda) y cobertura de más ítems por CA
- [ ] **Fase 6** — Login como proveedor para seguir sus cotizaciones enviadas

---

## 📜 Licencia y fuentes

- Código bajo licencia MIT (ver `LICENSE`).
- Datos: [API de Compra Ágil v2 de ChileCompra/Mercado Público](https://www.chilecompra.cl/api/)
  (documentación oficial: *Documentación API Compra Ágil*, mayo 2026).
- Precios: Solotodo y tiendas configuradas; cada resultado conserva su enlace.
