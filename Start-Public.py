import subprocess, re, time, os, sys

print()
print("  ============================================")
print("     Starting Orders Tracker Server...")
print("  ============================================")
print()

# Start Flask server
server = subprocess.Popen(
    [sys.executable, r"D:\OpenCode\webapp\app.py"],
    creationflags=subprocess.CREATE_NO_WINDOW
)
time.sleep(4)

print("  Server running on port 8081")
print()
print("  ============================================")
print("     Starting Cloudflare Tunnel...")
print("     Wait 10 seconds for the URL")
print("  ============================================")
print()

# Start cloudflared tunnel
tunnel = subprocess.Popen(
    [r"D:\OpenCode\webapp\cloudflared.exe", "tunnel", "--url", "http://localhost:8081"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

url = None
for line in tunnel.stdout:
    line = line.strip()
    m = re.search(r'(https://[^\s]+trycloudflare\.com)', line)
    if m:
        url = m.group(1)
        # Save URL to file
        with open(r"D:\OpenCode\webapp\PUBLIC_URL.txt", "w") as f:
            f.write(url)
        break

if url:
    print()
    print("  ============================================")
    print(f"  PUBLIC URL: {url}")
    print("  ============================================")
    print()
    print("  Share this link with anyone, anywhere!")
    print("  Just copy and send it.")
    print()
    print("  To close: close this window")
    print()
else:
    print("  ERROR: Could not start tunnel")

# Keep running
try:
    tunnel.wait()
except KeyboardInterrupt:
    pass
finally:
    server.terminate()
    tunnel.terminate()
