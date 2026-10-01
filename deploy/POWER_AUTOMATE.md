# Sending principal links automatically: Power Automate + WhatsApp Cloud API

When an admin moves a negotiation to a principal step (Item Identification, RFQ or Feedback I)
and leaves **"Send the link to the principal automatically"** ticked, the app:

1. creates a fresh link for that principal and revokes any older one;
2. posts one JSON event to a Power Automate flow;
3. lets the flow send the **email** (Outlook, from the procurement mailbox) and the **WhatsApp**
   template message (Meta WhatsApp Cloud API).

The app retries for about five hours, with gaps of 1, 5, 15, 60 and 240 minutes. Admins see each
message's status under **Negotiations → a principal → Activity → Messages**, with a **Retry**
button.

## 1. App settings (server environment variables)

| Variable | Example | Notes |
|---|---|---|
| `NEGO_PUBLIC_URL` | `https://nego.siloamhospitals.com` | The address principals open. It must be reachable from outside. |
| `NEGO_NOTIFY_WEBHOOK_URL` | the flow's HTTP POST URL | It contains a signature, so treat it like a password. |
| `NEGO_NOTIFY_SECRET` | any long random string | Optional. Adds `X-Nego-Signature: sha256=<HMAC of the body>` for tools that check it, such as n8n. |

Restart the app after setting them. The server must be allowed to make outbound HTTPS calls to
`*.logic.azure.com` / `*.environment.api.powerplatform.com`.

To check: as an admin, call `POST /api/admin/nego/notify/test` (or use the **Send link now**
button on a negotiation at a principal step). The flow should answer 200.

## 2. WhatsApp template (submit to Meta once)

In **WhatsApp Manager → Message templates → Create**:

- **Name:** `siloam_nego_step_open`
- **Category:** Utility
- **Language:** Indonesian (`id`)
- **Body:**
  ```
  Halo {{1}}, Siloam Hospitals mengundang Anda untuk {{2}} dalam negosiasi harga. Batas waktu: {{3}}.
  Silakan klik tombol di bawah. Tautan ini khusus untuk perusahaan Anda, mohon tidak diteruskan.
  ```
- **Button:** *Visit website*, dynamic URL `https://nego.siloamhospitals.com/p/{{1}}`
  (use your `NEGO_PUBLIC_URL`)
- **Samples for approval:** `{{1}}` = `Ibu Sari`, `{{2}}` = `Isi harga penawaran (RFQ)`,
  `{{3}}` = `15 Oktober 2026`; button `{{1}}` = `abc123`

The app sends these values as `whatsapp.body_params` (three items, in order) and
`whatsapp.button_param`.

You also need, from the Meta Business app: the **Phone number ID** and a **permanent access token**
(System User token with `whatsapp_business_messaging`). Keep the token in an Azure Key Vault secret
or a Power Platform environment variable of type *Secret*, never in the flow itself.

## 3. The flow

Create an **Automated cloud flow → When an HTTP request is received**.

1. **Trigger:** *Who can trigger the flow*: Anyone. The URL's signature protects it. Request Body
   JSON Schema:
   ```json
   {
     "type": "object",
     "properties": {
       "event": {"type": "string"},
       "principal": {"type": "object", "properties": {"name": {"type": "string"}, "distributor": {"type": ["string", "null"]}}},
       "recipient": {"type": "object", "properties": {
         "name": {"type": ["string", "null"]}, "email": {"type": ["string", "null"]}, "whatsapp": {"type": ["string", "null"]}}},
       "step": {"type": "object", "properties": {
         "key": {"type": "string"}, "number": {"type": "integer"}, "label_id": {"type": "string"},
         "label_en": {"type": "string"}, "due": {"type": ["string", "null"]}}},
       "link": {"type": "string"},
       "link_token": {"type": "string"},
       "email": {"type": "object", "properties": {"subject": {"type": "string"}, "html": {"type": "string"}}},
       "whatsapp": {"type": "object", "properties": {
         "to": {"type": ["string", "null"]}, "template": {"type": "string"}, "language": {"type": "string"},
         "body_params": {"type": "array", "items": {"type": "string"}}, "button_param": {"type": "string"}}}
     }
   }
   ```
2. **Condition:** `event` is equal to `principal.step_opened`. If not (for example the app's test
   event), go straight to step 5 and answer 200.
3. **If `recipient.email` is not empty:** *Office 365 Outlook → Send an email from a shared mailbox
   (V2)*.
   - Mailbox: the procurement mailbox
   - To: `recipient.email`
   - Subject: `email.subject`
   - Body: `email.html`
4. **If `recipient.whatsapp` is not empty:** *HTTP* action.
   - Method: `POST`
   - URI: `https://graph.facebook.com/v21.0/<PHONE_NUMBER_ID>/messages`
   - Headers: `Authorization: Bearer <token from Key Vault>`, `Content-Type: application/json`
   - Body:
     ```json
     {
       "messaging_product": "whatsapp",
       "to": "@{triggerBody()?['whatsapp']?['to']}",
       "type": "template",
       "template": {
         "name": "@{triggerBody()?['whatsapp']?['template']}",
         "language": {"code": "id"},
         "components": [
           {"type": "body", "parameters": [
             {"type": "text", "text": "@{triggerBody()?['whatsapp']?['body_params'][0]}"},
             {"type": "text", "text": "@{triggerBody()?['whatsapp']?['body_params'][1]}"},
             {"type": "text", "text": "@{triggerBody()?['whatsapp']?['body_params'][2]}"}]},
           {"type": "button", "sub_type": "url", "index": "0", "parameters": [
             {"type": "text", "text": "@{triggerBody()?['whatsapp']?['button_param']}"}]}
         ]
       }
     }
     ```
5. **Response:** status **200** when the actions above succeeded. Add a second *Response* with
   status **500**, set to *Configure run after → has failed*, so the app knows to retry.

**Licensing:** *When an HTTP request is received* and *HTTP* are **premium** connectors. The flow
owner needs Power Automate Premium (per user) or a per-flow plan.

## 4. Privacy

- **What the app sends:** only what the message needs, which is the principal's name, contact,
  step, due date and link. No prices, volumes or findings.
- **Who holds the link:**
  - It's valid for the number of days set in **Admin → Engine settings → Principal link stays
    valid for**.
  - It works only for that principal's open step.
  - An admin can revoke it at any time.
- **What's kept afterwards:** once a message is delivered, the app removes the link from its
  stored copy of the message.
- **Inside Power Automate:** turn off run-history inputs/outputs on the trigger and the HTTP
  action (*Settings → Secure inputs / Secure outputs*), so tokens and links don't show in the
  flow's run history.
