import os, sys, json, time, subprocess, tempfile
sys.path.insert(0, os.getcwd())
def say(*a): print("PROBE:", *a, flush=True)
say("platform", sys.platform, sys.version.split()[0])

# 1. Real PowerShell inventory end-to-end
try:
    from beerus_portable.windows_inventory import WindowsDriveEnumerator
    t0=time.time(); en = WindowsDriveEnumerator(); drives = en(); dt=time.time()-t0
    say("inventory seconds", round(dt,2), "count", len(drives))
    for d in drives:
        say("drive", repr(d.model)[:60], d.drive_class.value, d.size_bytes, "is_boot", d.is_boot, "identifiable", d.identifiable, "fp", d.fingerprint[:8])
    say("context system fps", len(en.context.system_fingerprints), "own fps", len(en.context.own_fingerprints), "letters", [l for l,_ in en.context.volume_letters])
except Exception as e:
    say("inventory FAILED", type(e).__name__, str(e)[:200])

# 2. st_ino on the runner's NTFS temp volume
try:
    td = tempfile.mkdtemp()
    inos=[]
    for n in ("a.txt","b.txt","c.txt"):
        p=os.path.join(td,n)
        open(p,"wb").write(b"x"*10)
        inos.append(os.lstat(p).st_ino)
    say("st_ino values", inos)
except Exception as e:
    say("st_ino FAILED", type(e).__name__, str(e)[:150])

# 3. Junction treatment in file scope
try:
    from beerus import filewipe
    base = tempfile.mkdtemp()
    root = os.path.join(base,"root"); outside = os.path.join(base,"outside")
    os.makedirs(root); os.makedirs(outside)
    open(os.path.join(root,"inner.txt"),"wb").write(b"i"*50)
    open(os.path.join(outside,"secret.txt"),"wb").write(b"s"*50)
    link = os.path.join(root,"linkdir")
    r = subprocess.run(["cmd","/c","mklink","/J",link,outside], capture_output=True, text=True)
    say("mklink rc", r.returncode, (r.stdout or r.stderr).strip()[:100])
    say("islink(junction)", os.path.islink(link), "isdir", os.path.isdir(link))
    scope = filewipe.build_file_scope([root], protected_roots=())
    for e in scope.entries: say("scope entry", e.kind, e.path.replace(base,"BASE"), e.size_bytes)
    say("scope total_files", scope.total_files)
except Exception as e:
    say("junction FAILED", type(e).__name__, str(e)[:200])

# 4. Raw physical drive reads: aligned vs unaligned
try:
    fd = os.open(r"\\.\PhysicalDrive0", os.O_RDONLY | getattr(os,"O_BINARY",0))
    try:
        os.lseek(fd,0,os.SEEK_SET); data=os.read(fd,512); say("aligned read 512 ok", len(data))
    except OSError as e: say("aligned read FAILED", e.errno, str(e)[:100])
    try:
        os.lseek(fd,1000,os.SEEK_SET); data=os.read(fd,512); say("unaligned read ok", len(data))
    except OSError as e: say("unaligned read FAILED", e.errno, str(e)[:100])
    os.close(fd)
except OSError as e:
    say("physicaldrive open FAILED", e.errno, str(e)[:120])

# 5. Deep path (>260) file wipe end-to-end through the real engine
try:
    from beerus import filewipe
    base = tempfile.mkdtemp()
    deep = base
    while len(deep) < 250: deep = os.path.join(deep, "d"*20)
    os.makedirs(deep)
    target = os.path.join(deep, "victim.txt")
    open(target,"wb").write(b"z"*1234)
    say("deep path length", len(target))
    scope = filewipe.build_file_scope([target], protected_roots=())
    auth = filewipe.FileGateAuthority(); sess = auth.begin_session(scope); sess.acknowledge_scope()
    tok = sess.final_confirmation(scope.expected_phrase)
    res = filewipe.FileWipeEngine(auth, protected_roots=()).execute(scope, tok)
    say("deep wipe outcomes", [(o.status, o.bytes_written) for o in res.outcomes], "exists after", os.path.exists(target))
except Exception as e:
    say("deep FAILED", type(e).__name__, str(e)[:200])
say("DONE")
