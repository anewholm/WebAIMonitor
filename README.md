# Web AI Monitor
A Python cron job that uses Claude AI to handle Project Management conversations with clients about software present on the computer. 
The Python script checks registered Software repositories for a `PM.md` file containing online contact details. If found, it will monitor those conversations.
When a new message is detected the Python will use the Claude SDK API to initiate an AI conversation with the client.
Currently only supports WhatsApp web. Currently only scans `/var/www/*/PM.md` and `~Software/*/PM.md`.
