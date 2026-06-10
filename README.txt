Bluebeam Markup Processor — Streamlit Deployment Guide
=======================================================

FILES IN THIS PACKAGE
----------------------
  streamlit_app.py          Main application
  requirements.txt          Python dependencies
  .streamlit/config.toml    App configuration (upload size, theme)
  README_streamlit.txt      This file


OPTION A — STREAMLIT COMMUNITY CLOUD (recommended, free)
----------------------------------------------------------
Best for: teams that want a shared URL with no server to manage.

1. Create a free account at https://share.streamlit.io

2. Push these files to a GitHub repository:
     your-repo/
       streamlit_app.py
       requirements.txt
       .streamlit/
         config.toml

3. In Streamlit Community Cloud:
     - Click "New app"
     - Connect your GitHub repo
     - Set Main file path: streamlit_app.py
     - Click "Deploy"

4. Share the generated URL (e.g. https://yourname-app.streamlit.app)
   with your team. Anyone with the URL can use it — no login needed
   unless you enable access controls.

Notes:
  - Free tier allows one private app; additional apps are public.
  - The app sleeps after ~1 week of inactivity and wakes on next visit.
  - No data is stored — files exist only in memory during processing.
  - Max upload size is set to 200MB in config.toml. Streamlit Cloud
    enforces a hard cap of 200MB regardless.


OPTION B — RUN LOCALLY (one person or small team on same machine)
------------------------------------------------------------------
Best for: personal use or quick sharing on a LAN.

1. Install dependencies once:
     pip install -r requirements.txt

2. Run:
     streamlit run streamlit_app.py

3. Browser opens automatically at http://localhost:8501
   Others on your network can reach it at http://YOUR_IP:8501


OPTION C — SELF-HOSTED SERVER (IT-managed, always-on)
------------------------------------------------------
Best for: larger teams that want an internal tool on company
infrastructure without relying on external services.

Requirements: A Linux server (VM or bare metal) with Python 3.10+

1. Copy files to server:
     scp streamlit_app.py requirements.txt user@server:/opt/bb-processor/
     scp -r .streamlit user@server:/opt/bb-processor/

2. On server:
     cd /opt/bb-processor
     pip install -r requirements.txt
     streamlit run streamlit_app.py --server.port 8501 --server.address 0.0.0.0

3. Open firewall port 8501 (or put behind nginx/a reverse proxy on port 80/443)

4. To keep it running permanently, create a systemd service:

     [Unit]
     Description=Bluebeam Markup Processor
     After=network.target

     [Service]
     User=www-data
     WorkingDirectory=/opt/bb-processor
     ExecStart=/usr/bin/python3 -m streamlit run streamlit_app.py \
               --server.port 8501 --server.address 0.0.0.0
     Restart=always

     [Install]
     WantedBy=multi-user.target

   Save as /etc/systemd/system/bb-processor.service, then:
     systemctl enable bb-processor
     systemctl start bb-processor

5. (Optional) Put nginx in front for HTTPS:
     proxy_pass http://localhost:8501;


OPTION D — DOCKER (portable, works anywhere Docker runs)
---------------------------------------------------------
Best for: teams already using Docker, or cloud deployment (AWS, Azure, GCP).

Create a Dockerfile alongside your app files:

    FROM python:3.11-slim
    WORKDIR /app
    COPY requirements.txt .
    RUN pip install --no-cache-dir -r requirements.txt
    COPY streamlit_app.py .
    COPY .streamlit/ .streamlit/
    EXPOSE 8501
    CMD ["streamlit", "run", "streamlit_app.py", \
         "--server.port=8501", "--server.address=0.0.0.0"]

Build and run:
    docker build -t bb-processor .
    docker run -p 8501:8501 bb-processor

Then access at http://localhost:8501 or share the server IP.


UPDATING THE APP
----------------
1. Edit streamlit_app.py
2. If using Streamlit Cloud: push to GitHub — it redeploys automatically
3. If self-hosted: copy the new file to the server and restart the service
4. If Docker: rebuild the image and restart the container


SECURITY NOTES
--------------
- Files uploaded by users are processed entirely in memory and never
  written to disk on the server. They are discarded after the response
  is sent.
- If deploying internally, consider restricting access by IP or putting
  the app behind your company's SSO/VPN.
- Streamlit Community Cloud supports Google OAuth and email-based access
  controls under Settings → Sharing in the dashboard.


TROUBLESHOOTING
---------------
Problem : "File too large" error on upload
Fix     : Increase maxUploadSize in .streamlit/config.toml and redeploy.
          Note Streamlit Cloud has a hard 200MB limit.

Problem : App shows blank page or errors on startup
Fix     : Check the app logs in Streamlit Cloud dashboard, or run locally
          to see the full traceback.

Problem : Markups not updating after download
Fix     : Make sure to close and reopen the PDF in Bluebeam Revu after
          replacing the file. Revu caches open documents.

Problem : "Column not found" error
Fix     : Click "Load columns" after uploading the Excel file to confirm
          the column names. Check that the header row number is correct.
