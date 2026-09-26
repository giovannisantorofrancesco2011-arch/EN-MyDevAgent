# MyDevAgent in Cursor

Cursor sends custom model requests **from its own servers**, not from your PC: that's why it can't
reach `localhost`. You need a temporary public URL pointing to your local server.

1. Set a key (mandatory if you expose the server!):
   ```bash
   export MYDEVAGENT_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
   mydevagent serve
   ```
2. Open an HTTPS tunnel (pick one):
   ```bash
   cloudflared tunnel --url http://127.0.0.1:8000     # → https://xxxx.trycloudflare.com
   # or: ngrok http 8000
   ```
3. Cursor → **Settings → Models**:
   - **OpenAI API Key**: your `MYDEVAGENT_API_KEY`
   - **Override OpenAI Base URL**: `https://xxxx.trycloudflare.com/v1`
   - **Add model**: `mydevagent`, `mydevagent-fast`, `mydevagent-deep`
4. In Cursor's chat select `mydevagent`.

Limits: Tab/autocomplete and some of Cursor's agent features only use Cursor's models. Your code
goes through the tunnel: if you want to stay 100% offline use VS Code + Continue.
