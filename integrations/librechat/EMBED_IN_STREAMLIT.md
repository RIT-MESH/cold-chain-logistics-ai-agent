# Embedded LibreChat in Streamlit

The `AI Assistant (LibreChat)` sidebar page now embeds LibreChat in an iframe within the existing Streamlit application. Grafana has a separate embed view; the older direct Streamlit chat has been retired.

## Configure Streamlit

Set `LIBRECHAT_PUBLIC_URL` in the project root `.env` file, for example:

```dotenv
LIBRECHAT_PUBLIC_URL=http://localhost:3080
```

In production, use a real HTTPS URL that browsers can reach, for example `https://chat.example.com`. A Docker-only address like `http://librechat:3080` will not work in a browser iframe. For a remote user, `localhost` points to their own computer, not the server.

## Allow LibreChat to be framed safely

Recent LibreChat versions include security headers and a content-security-policy configuration. LibreChat defaults to denying cross-origin framing. **Do not disable all security headers.** For separate origins, in LibreChat's environment configure:

```dotenv
SECURITY_HEADERS=true
CSP_ENABLED=true
CSP_REPORT_ONLY=false
X_FRAME_OPTIONS=off
CSP_FRAME_ANCESTORS="'self' https://dashboard.example.com"
```

Replace `https://dashboard.example.com` with the **exact** Streamlit origin (scheme, hostname and any port), e.g. `http://localhost:8501` for a local development setup. For local-only development use:

```dotenv
CSP_FRAME_ANCESTORS="'self' http://localhost:8501"
```

`X_FRAME_OPTIONS=off` must only be used **with an enforced frame-ancestors CSP**, or the site can be framed by untrusted origins. If a reverse proxy sets its own `X-Frame-Options`/CSP headers, align them to these restrictions instead of layering conflicting headers.

## Authentication and production considerations

- LibreChat still presents its own login, unless properly configured for your identity provider. Streamlit login does **not** automatically sign users into LibreChat.
- Cross-site third-party cookie restrictions may break authentication if the applications are on unrelated domains. Preferred deployment: `dashboard.example.com` and `chat.example.com` behind HTTPS with carefully configured session cookies and an SSO strategy. Modern browser privacy settings may still block some third-party cookies.
- Verify embedding on the browsers your dispatchers use. Check the browser developer console for frame or cookie errors.
- Embedding the interface does not change backend authorization: retain FastAPI API-key protection, allowlisted queries, and user-level permission enforcement before production use.
- LibreChat, SQL Server, Pinecone, and LLMs must be configured and running. Embedding itself is not an end-to-end verification.

## Launch

Run LibreChat separately as described in `integrations/librechat/README.md`; then start Streamlit:

```bash
streamlit run src/ui.py
```

Go to **AI Assistant (LibreChat)** in the Streamlit sidebar.

## References
- https://docs.streamlit.io/develop/api-reference/text/st.iframe
- https://www.librechat.ai/docs/configuration/security_headers
