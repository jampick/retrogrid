#!/usr/bin/env python3
"""Film the console for docs/media: headless Chromium driven over CDP.

    scripts/capture_media.py URL OUTDIR STEPS

STEPS is a comma list:  wait:SECS | key:K | shot:NAME | rec:SECS
  shot -> OUTDIR/NAME.png       rec -> OUTDIR/f%05d.jpg + frames.txt (ffmpeg concat, real timestamps)

    retrogrid --no-window --port 8084 --league stub &
    scripts/capture_media.py 'http://127.0.0.1:8084/?ffb=0' /tmp/take 'wait:7,key:3,wait:3,rec:60'
    ffmpeg -f concat -safe 0 -i /tmp/take/frames.txt -ss 31 -t 16 -filter_complex \
      "fps=12,scale=1000:-1:flags=lanczos,hqdn3d=4:4:8:8,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=none" hero.gif

hqdn3d + dither=none matter: the CRT grain changes every pixel every frame, which a GIF cannot afford.
Env: W, H (1600x900), CDP (debug port). Needs `websockets` (comes with uvicorn[standard]) and chromium on PATH.
"""
import asyncio, base64, json, os, subprocess, sys, tempfile, time, urllib.request
import websockets

URL, OUT, SCRIPT = sys.argv[1], sys.argv[2], sys.argv[3]
W, H = int(os.environ.get("W", 1600)), int(os.environ.get("H", 900))
PORT = int(os.environ.get("CDP", 9333))
KEYS = {"Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, ""), "Escape": ("Escape", 27, ""), "ArrowRight": ("ArrowRight", 39, ""),
        "ArrowLeft": ("ArrowLeft", 37, ""), "ArrowDown": ("ArrowDown", 40, ""), "Space": (" ", 32, " ")}

async def main():
    os.makedirs(OUT, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="rgcap")
    proc = subprocess.Popen(["chromium", "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
        f"--window-size={W},{H}", "--hide-scrollbars", "--mute-audio", "--no-first-run", "--force-device-scale-factor=1",
        "--autoplay-policy=no-user-gesture-required", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json")); break
            except Exception: time.sleep(0.2)
        ws_url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]
        async with websockets.connect(ws_url, max_size=None) as ws:
            n = 0; fi = 0; casting = False; stamps = []
            async def frame(m):
                nonlocal n, fi
                pr = m["params"]
                open(f"{OUT}/f{fi:05d}.jpg", "wb").write(base64.b64decode(pr["data"]))
                stamps.append((fi, pr["metadata"]["timestamp"])); fi += 1
                n += 1; await ws.send(json.dumps({"id": n, "method": "Page.screencastFrameAck", "params": {"sessionId": pr["sessionId"]}}))
            async def call(method, **params):
                nonlocal n; n += 1; i = n
                await ws.send(json.dumps({"id": i, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == i: return m.get("result", {})
                    if m.get("method") == "Page.screencastFrame": await frame(m)
            await call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
            await call("Page.navigate", url=URL)
            for step in SCRIPT.split(","):
                op, _, arg = step.partition(":")
                if op == "wait": await asyncio.sleep(float(arg))
                elif op == "key":
                    key, code, text = KEYS[arg] if arg in KEYS else (arg, ord(arg.upper()), arg)
                    await call("Input.dispatchKeyEvent", type="keyDown", key=key, windowsVirtualKeyCode=code, text=text)
                    await call("Input.dispatchKeyEvent", type="keyUp", key=key, windowsVirtualKeyCode=code)
                elif op == "shot":
                    r = await call("Page.captureScreenshot", format="png")
                    open(f"{OUT}/{arg}.png", "wb").write(base64.b64decode(r["data"])); print("shot", arg, flush=True)
                elif op == "rec":
                    if not casting:
                        await call("Page.startScreencast", format="jpeg", quality=95, everyNthFrame=1); casting = True
                    end = time.monotonic() + float(arg); k = 0
                    while time.monotonic() < end:
                        try: m = json.loads(await asyncio.wait_for(ws.recv(), 0.25))
                        except asyncio.TimeoutError: continue
                        if m.get("method") != "Page.screencastFrame": continue
                        await frame(m); k += 1
                    print("rec", k, "frames", f"{k/float(arg):.1f}fps", flush=True)
            with open(f"{OUT}/frames.txt", "w") as f:
                for j, (i, t) in enumerate(stamps):
                    d = (stamps[j + 1][1] - t) if j + 1 < len(stamps) else 0.05
                    f.write("file 'f%05d.jpg'\nduration %.4f\n" % (i, max(d, 0.001)))
    finally:
        proc.terminate()

asyncio.run(main())
