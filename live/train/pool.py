import glob, os, re, shutil, random
ROBOT={"robot","Robot","Robots","red_alliance","blue_alliance","unknown_alliance","BLU","RED","Blue-Robot","Red-Robot","red_bumper","blue_bumper"}
rfre=re.compile(r"[._](jpg|png|jpeg)\.rf\.[0-9a-f]+\.(jpg|png|jpeg)$",re.I)
root="pool"; shutil.rmtree(root,ignore_errors=True)
for s in ("train","val"): os.makedirs(f"{root}/images/{s}"); os.makedirs(f"{root}/labels/{s}")
random.seed(0); n=0
for loc in sorted(glob.glob("rf/*")):
    y=open(f"{loc}/data.yaml").read()
    names=[x.strip("- \x27\"\n") for x in y.split("names:")[1].split("nc:")[0].strip().split("\n") if x.strip()] if "nc:" in y.split("names:")[1] else None
    if names is None or len(names)<=1:
        import yaml; names=yaml.safe_load(y)["names"]; names=list(names.values()) if isinstance(names,dict) else names
    keep={i for i,nm in enumerate(names) if nm in ROBOT}
    seen=set()
    for im in sorted(glob.glob(f"{loc}/*/images/*.*")):
        st=rfre.sub("",os.path.basename(im))
        if st in seen: continue
        lb=im.replace("/images/","/labels/").rsplit(".",1)[0]+".txt"
        if not os.path.exists(lb): continue
        rows=[l.split() for l in open(lb) if len(l.split())==5 and int(l.split()[0]) in keep]
        if not rows: continue
        seen.add(st); n+=1; sp="val" if random.random()<0.08 else "train"
        o=f"{os.path.basename(loc)}_{n:05d}"
        shutil.copy(im,f"{root}/images/{sp}/{o}{os.path.splitext(im)[1]}")
        open(f"{root}/labels/{sp}/{o}.txt","w").write("".join("0 "+" ".join(r[1:5])+"\n" for r in rows))
    print(loc, "keep", [names[i] for i in keep], "total", n)
open(f"{root}/data.yaml","w").write(f"path: {os.path.abspath(root)}\ntrain: images/train\nval: images/val\nnc: 1\nnames: [robot]\n")
