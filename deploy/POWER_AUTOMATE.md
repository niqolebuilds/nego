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
| `NEGO_NOTIFY_ADMINS` | `buyer@siloamhospitals.com, head.procurement@siloamhospitals.com` | Who gets "principal sent a step" notices and the weekly MOU alert. |

Restart the app after setting them. The server must be allowed to make outbound HTTPS calls to
`*.logic.azure.com` / `*.environment.api.powerplatform.com`.

To check: as an admin, call `POST /api/admin/nego/notify/test` (or use the **Send link now**
button on a negotiation at a principal step). The flow should answer 200.

## Events the app sends

| `event` | When | To | WhatsApp template |
|---|---|---|---|
| `principal.step_opened` | An admin moves the negotiation to a principal step | Principal: **email first**; WhatsApp too only when *WhatsApp nudge after* is 0 or there is no email address | `siloam_nego_step_open` |
| `principal.whatsapp_nudge` | The principal hasn't opened the link or done anything for *WhatsApp nudge after* days (default 2); once per step | Principal: **WhatsApp only** (`recipient.email` is null) | `siloam_nego_step_open` |
| `principal.reminder` | 3 and 1 days before the step deadline, then daily while late, until the principal sends | Principal: email; WhatsApp too once the nudge has gone | `siloam_nego_reminder` |
| `siloam.step_submitted` | A principal sends a step | `NEGO_NOTIFY_ADMINS`: email only | none |
| `siloam.mou_alert` | Weekly (Monday by default), when some MOUs end within 6 months with no negotiation open | `NEGO_NOTIFY_ADMINS`: email only | none |
| `test` | The admin's test call | none; just answer 200 | none |

- **Reminder timing:** reminder days, the default step deadline and the alert weekday are set in
  **Admin → Engine settings**.
- **Same link:** a reminder or nudge carries the same link the principal already got.
- **Which channel:** the app decides by filling or emptying `recipient.email` and `recipient.whatsapp`. The flow
  needs no change: it already sends the email only when `recipient.email` is not empty and the WhatsApp
  message only when `recipient.whatsapp` is not empty. In the Parse JSON schema, allow `null` for the
  `email` and `whatsapp` blocks (`"type": ["object", "null"]`), because the nudge has no email block.
- **Setting:** *WhatsApp nudge after (days)* in **Admin → Engine settings**. 0 sends email and WhatsApp together, as before.
- **Never twice:** the app never sends the same reminder twice on one day, even after a restart.
- **Recipients for Siloam's emails:** `siloam.*` events list them in `email.to`, an array; send
  one email per address or join them with `;`.

## 2. WhatsApp templates (submit to Meta once)

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

**Second template**, for reminders:

- **Name:** `siloam_nego_reminder`
- **Category:** Utility
- **Language:** `id`
- **Body:**
  ```
  Halo {{1}}, pengingat dari Siloam Hospitals: {{2}} belum dikirim. Batas waktu {{3}} ({{4}}).
  Silakan klik tombol di bawah untuk melanjutkan. Data yang sudah diisi tersimpan.
  ```
- **Button:** the same dynamic URL as the first template.
- **Samples:** `{{1}}` = `Ibu Sari`, `{{2}}` = `Isi harga penawaran (RFQ)`,
  `{{3}}` = `15 Oktober 2026`, `{{4}}` = `1 hari lagi`

For reminders, `whatsapp.body_params` has **four** items.

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
       "email": {"type": ["object", "null"], "properties": {"subject": {"type": "string"}, "html": {"type": "string"}}},
       "whatsapp": {"type": ["object", "null"], "properties": {
         "to": {"type": ["string", "null"]}, "template": {"type": "string"}, "language": {"type": "string"},
         "body_params": {"type": "array", "items": {"type": "string"}}, "button_param": {"type": "string"}}}
     }
   }
   ```
2. **Switch** on `event`:
   - **`principal.step_opened` and `principal.reminder`:** steps 3 and 4 below. The reminder
     uses the second template and four body parameters.
   - **`siloam.step_submitted` and `siloam.mou_alert`:** only *Send an email (V2)*, with To set
     to `join(triggerBody()?['email']?['to'], ';')`, plus Subject and Body from `email`.
   - **Default (for example `test`):** go straight to step 5 and answer 200.
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
