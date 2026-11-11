# Hosting "Where should we eat?" on Unraid — step by step

When you're done, the two of you open **https://eat.gulu.stream** on your phones.

```
Phone ──► Cloudflare (eat.gulu.stream) ──► SWAG (on Unraid, port 443)
              └─► eat container  192.168.1.230:8766   (data in /mnt/user/appdata/eat)
                    └─► Ollama   192.168.1.230:11434  (only for the Ask page)
```

Same setup as the D&D Companion (`dnd.gulu.stream`), on the next port up (8766). Do the
parts in order; each ends with a **✅ Check**.

Nothing needs moving from your PC: the server starts with an empty database, and you make
your logins there.

---

## Part 1 — Publish the image

The code has to be on GitHub once; GitHub then builds the Docker image by itself.

1. Push this folder to **https://github.com/mglavelle/eat** (branch `main`).
2. On GitHub open the repo → **Actions** tab → wait for **Docker image** to show a green ✓
   (about 3 minutes).
3. Repo page → right-hand side → **Packages** → **eat** → **Package settings** → at the
   bottom, **Change visibility** → **Public**. (Skip if it already says Public. A private
   package makes Unraid's download fail with "denied".)

✅ **Check:** https://github.com/mglavelle/eat/pkgs/container/eat shows a `latest` tag.

---

## Part 2 — Create the app's folder on the server

1. Open the Unraid web UI (http://192.168.1.230) → click the **>_** icon (top right).
2. Paste these lines and press **Enter**:

   ```bash
   mkdir -p /mnt/user/appdata/eat
   chown -R nobody:users /mnt/user/appdata/eat
   chmod -R u+rwX,g+rwX /mnt/user/appdata/eat
   ```

✅ **Check:** `ls -ld /mnt/user/appdata/eat` shows `nobody users`.

---

## Part 3 — Install the container

### 3a. Put the template on the server

1. In WinSCP, go to **`/boot/config/plugins/dockerMan/templates-user/`**.
2. In File Explorer, open `C:\Users\lavel\Documents\__WORK\Eat\docs\unraid\`.
3. Drag **`my-eat.xml`** into the WinSCP window → **OK**.

### 3b. Create the container

1. Unraid web UI → **Docker** tab → bottom of the page → **Add Container**.
2. **Template** dropdown → under **User templates** choose **eat**. The form fills in.
3. Check the fields:

   | Field | Should be |
   |---|---|
   | Repository | `ghcr.io/mglavelle/eat:latest` |
   | Network Type | `Bridge` |
   | Web UI port | `8766` |
   | Data | `/mnt/user/appdata/eat` |
   | Ollama URL | `http://192.168.1.230:11434` (the Ollama container from the D&D guide) |
   | GPU layers | `99` |
   | Google Places key | leave empty for now (see "Google ratings" below) |

4. Toggle **Advanced View** (top right) and check **Extra Parameters** is `--user 99:100`.
5. **Apply** → wait for **Command execution completed successfully** → **Done**.

✅ **Check (container):** on the **Docker** tab, `eat` is green/started. Its **Logs** end with
`Uvicorn running on http://0.0.0.0:8000`.

### 3c. Make your logins — before it goes on the internet

The very first visitor gets to create the first login, so do this now, at home:

1. On your PC open **http://192.168.1.230:8766**.
2. Pick your name and a real password (6+ characters) → **Create my login**.
3. You land on **Settings**. Under **People**, add your girlfriend's login.
4. Under **Where you are**, type an address or a nearby corner → **Look up** → **Use this**.
   The search runs (10–60 seconds) and says how many places it found.

✅ **Check:** the **Tonight** tab shows restaurants near you, and **Ask** answers a question
(first answer ~20–30 s while the model loads).

> If the search says OpenStreetMap's servers are busy, your location is still saved. Wait
> a few minutes and press **Save and search again**.

---

## Part 4 — Add the DNS record in Cloudflare

1. https://dash.cloudflare.com → **gulu.stream** → **DNS** → **Records**.
2. Find the **`dnd`** (or `watch`) row → **Edit** → copy the **IPv4 address** → **Cancel**.
3. **+ Add record**: Type `A`, Name `eat`, IPv4 = the address you copied, Proxy status
   **on** (orange cloud), TTL `Auto` → **Save**.

✅ **Check:** the list shows `eat.gulu.stream  A  …  Proxied`.

Your SWAG certificate is a wildcard (`*.gulu.stream`), so nothing changes for HTTPS.

---

## Part 5 — Tell SWAG about the new site

1. In WinSCP, go to **`/mnt/user/appdata/swag/nginx/proxy-confs/`** (where
   `dnd.subdomain.conf` is).
2. From `C:\Users\lavel\Documents\__WORK\Eat\docs\unraid\`, drag
   **`eat.subdomain.conf`** into WinSCP → **OK**. Its contents:

   ```nginx
   server {
       listen 443 ssl;
       server_name eat.gulu.stream;

       client_max_body_size 100m;

       location / {
           proxy_pass http://192.168.1.230:8766;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
           proxy_redirect off;

           proxy_http_version 1.1;
           proxy_buffering off;
           proxy_request_buffering off;
           proxy_read_timeout 300s;
       }
   }
   ```

3. Unraid **Docker** tab → **swag** icon → **Restart**.
4. In the Unraid terminal:

   ```bash
   docker exec swag nginx -t
   ```

✅ **Check:** the output ends with **`test is successful`**.

What the extra lines are for: `client_max_body_size` lets the Google Takeout upload through
(nginx stops at 1 MB otherwise), `proxy_buffering off` lets Ask answers appear word by word,
and `proxy_read_timeout` stops nginx cutting off a slow search.

---

## Part 6 — Test from outside

1. On your phone, **turn Wi-Fi off**.
2. Open **https://eat.gulu.stream** → sign in → filter → **Pick for us**.

✅ **Check:** it works on mobile data. On the phone, **Settings → Use this device's location**
now works too (browsers only share location over https).

### Optional: lock it to the two of you (Cloudflare Access)

The site has its own logins, so this is an extra layer rather than a must. It is the same as
Part 5b of the D&D guide: Zero Trust → **Access** → **Applications** → **Add an
application** → **Self-hosted** → hostname `eat` / `gulu.stream` → policy **Allow** →
**Emails** → your two addresses → **Save**. No bypass rule is needed for this site.

---

## Google ratings, prices and "open now" (optional)

Without a key the site uses OpenStreetMap: free, but no ratings, prices or opening status.
To switch to Google:

1. https://console.cloud.google.com → create a project → enable **Places API (New)** →
   set up billing → **Credentials** → **Create API key**. Restrict the key to
   *Places API (New)*.
2. Unraid **Docker** tab → **eat** icon → **Edit** → paste the key into **Google Places
   key** → **Apply**.
3. On the site: **Settings** → **Save and search again**.

A search makes about 23 requests and the results are kept, so a few searches a month stay
inside Google's free allowance. Check the current allowance in the Cloud console before
relying on that. Favorites and meal history are stored per restaurant source, so set the key
**before** you build up history, or you will be marking favorites again.

---

## Updating later

1. Push new code to GitHub → the image rebuilds (~3 min, **Actions** tab shows ✓).
2. Unraid → **Docker** tab → **Check for Updates** → `eat` → **apply update**.

Your data in `/mnt/user/appdata/eat` is not touched by updates. **Back up**
`/mnt/user/appdata/eat/eat.db` (the *Appdata Backup* plugin includes it automatically).

---

## Troubleshooting

| What you see | What to do |
|---|---|
| Unraid says `denied` or `manifest unknown` when pulling | Part 1 step 3: make the package public, and check the Actions run is green. |
| Container stops right after starting; log says `unable to open database file` | Redo Part 2 (ownership) and check **Extra Parameters** is `--user 99:100`. |
| It asks you to create a login again after an update | The **Data** path is not `/mnt/user/appdata/eat`, so the database was not kept. |
| "OpenStreetMap's free search servers are all busy" | They are volunteer-run. Try again in a few minutes; the location is already saved. |
| Ask says it could not reach Ollama | **Ollama URL** must be `http://192.168.1.230:11434`, and that address must say "Ollama is running" in a browser. The rest of the site works without it. |
| Ask is very slow (~5 words a second) | **GPU layers** should be `99` (see the D&D guide, Part 3d). |
| **502 Bad Gateway** | The container is not answering on `192.168.1.230:8766`: is it started? Does that address work at home? |
| Cloudflare error **521/522** | SWAG did not restart cleanly: redo Part 5 steps 3–4. |
| Upload fails with **413** | The file is over 100 MB (Cloudflare's limit). In Takeout tick only *Maps (your places)* and *Saved*, or upload at home through `http://192.168.1.230:8766`. |
| "Open now" or "today" is hours off | The container's time zone: Unraid sets `TZ` from **Settings → Date and Time**. |
