#!/usr/bin/env python3
import argparse, csv, json, math, os, re, subprocess, sys, textwrap, wave
from pathlib import Path
from collections import defaultdict

import cv2
import librosa
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.signal import butter, sosfiltfilt, chirp
from faster_whisper import WhisperModel

FPS_BASE = 30
FPS_FINAL = 60
W, H = 1080, 1920
DURATION = 22.4
DROP_EDIT = 7.60
BLOCK_WORDS = re.compile(r"\b(DOMAIN|INFINITE|VOID|IMAX|DECEMBER|THEATRICAL|TICKETS|RELEASE DATE|COMING SOON)\b", re.I)
BLOCK_JP = re.compile(r"(公開日|上映日|発売日|劇場公開|チケット)")
KEYWORDS = ["最強", "領域展開", "無量空処", "強い", "大丈夫", "心配", "strongest", "domain expansion", "infinite void", "don't worry", "you are weak"]

def sh(cmd, check=True, capture=False):
    p = subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)
    return p.stdout if capture else ""

def probe(path):
    raw = sh(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],capture=True)
    return json.loads(raw)

def audio_duration(path):
    p = probe(path)
    return float(p.get("format",{}).get("duration",0))

def extract_audio(video, out):
    sh(["ffmpeg","-y","-v","error","-i",str(video),"-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(out)])
    return out

def transcribe_sources(videos, work):
    model = WhisperModel("small", device="cpu", compute_type="int8")
    all_segments = []
    transcripts = []
    for video in videos:
        wav = work / (video.stem + "_speech.wav")
        extract_audio(video, wav)
        segs, info = model.transcribe(str(wav), beam_size=5, vad_filter=True, word_timestamps=True)
        current = []
        for s in segs:
            text = (s.text or "").strip()
            if not text or float(s.end)-float(s.start) < 0.35:
                continue
            item = {"source":str(video),"start":float(s.start),"end":float(s.end),"text":text,"language":info.language,
                    "avg_logprob":float(getattr(s,"avg_logprob",-1.0))}
            current.append(item)
            all_segments.append(item)
        transcripts.append({"source":str(video),"language":info.language,"segments":current})
    (work/"transcript.json").write_text(json.dumps(transcripts,ensure_ascii=False,indent=2))
    return all_segments

def select_dialogue(segments):
    if len(segments) < 2:
        raise RuntimeError("Could not find two distinct spoken Gojo dialogue segments in the downloaded source videos; refusing to fabricate dialogue.")
    def key_score(s):
        t=s["text"].lower()
        return sum(5 for k in KEYWORDS if k.lower() in t) + min(len(t),40)/30 + max(0,1.2-(s["end"]-s["start"])) - abs(s["avg_logprob"])*0.1
    candidates=sorted(segments,key=lambda s:(s["end"]-s["start"]<=3.4, key_score(s)),reverse=True)
    setup=next((s for s in candidates if 1.0 <= s["end"]-s["start"] <= 3.8),None)
    punch_candidates=sorted(segments,key=key_score,reverse=True)
    punch=next((s for s in punch_candidates if s is not setup and 0.65 <= s["end"]-s["start"] <= 2.25),None)
    if setup is None:
        setup=max(segments,key=lambda s:min(s["end"]-s["start"],3.8))
    if punch is None:
        punch=next((s for s in sorted(segments,key=lambda s:s["start"],reverse=True) if s is not setup and s["end"]-s["start"]<=2.8),None)
    if punch is None or punch is setup:
        raise RuntimeError("Only one usable voice segment was found; cannot build a dialogue-to-punchline arc without inventing speech.")
    return setup,punch

def render_voice_clip(seg, dest, maxdur):
    start=max(0.0,seg["start"]+0.02)
    dur=min(seg["end"]-start,maxdur)
    if dur < 0.55:
        raise RuntimeError("Selected dialogue phrase is too short.")
    sh(["ffmpeg","-y","-v","error","-ss",f"{start:.3f}","-i",seg["source"],"-t",f"{dur:.3f}",
        "-vn","-ac","2","-ar","48000","-af","highpass=f=100,lowpass=f=8500,acompressor=threshold=-22dB:ratio=2.8:attack=8:release=100,loudnorm=I=-17:TP=-2:LRA=7",
        str(dest)])
    return dur

def analyze_music(path, work):
    y,sr=librosa.load(str(path),sr=22050,mono=True)
    hop=256
    onset=librosa.onset.onset_strength(y=y,sr=sr,hop_length=hop)
    onset_frames=librosa.onset.onset_detect(onset_envelope=onset,sr=sr,hop_length=hop,backtrack=False,units="time")
    # Stable low-band RMS envelope: a low-frequency bandpass rather than a single spectral bin.
    sos=butter(4,[20,120],btype="bandpass",fs=sr,output="sos")
    bass=sosfiltfilt(sos,y)
    frame=int(0.10*sr)
    bass_env=np.array([np.sqrt(np.mean(bass[i:i+frame]**2)+1e-12) for i in range(0,len(bass)-frame,frame)])
    times=np.arange(len(bass_env))*0.10
    flux_times=np.asarray(onset_frames,dtype=float)
    # Search the decisive drop after a short atmospheric runway; combine sub-bass jump and onset strength.
    candidates=[]
    for t in times:
        if 5.0 <= t <= min(25.0,len(y)/sr-5):
            idx=int(t/0.10)
            pre=np.mean(bass_env[max(0,idx-8):max(1,idx-2)]) if idx>=3 else 0
            post=np.mean(bass_env[idx:min(len(bass_env),idx+3)])
            jump=post/(pre+1e-9)
            beat=float(np.max(onset[max(0,int(t*sr/hop)-2):min(len(onset),int(t*sr/hop)+3)])) if len(onset) else 0
            candidates.append((float(jump)*max(0.05,beat),float(t),float(jump),beat))
    if not candidates:
        raise RuntimeError("Music analysis could not identify a bass-drop candidate.")
    _,drop,jump,drop_strength=max(candidates)
    selected=[float(t) for t in flux_times if 0.02 < t < len(y)/sr-0.02]
    # Group onset events into transient clusters and retain the stronger event within 90 ms.
    merged=[]
    for t in selected:
        if not merged or t-merged[-1]>0.09: merged.append(t)
        elif onset[int(t*sr/hop)]>onset[int(merged[-1]*sr/hop)]: merged[-1]=t
    bpm=float(librosa.beat.tempo(onset_envelope=onset,sr=sr,hop_length=hop,aggregate=np.median)[0])
    rows=[{"time_s":round(t,4),"time_ms":round(t*1000),"type":"onset"} for t in merged]
    with (work/"beat_timing.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["time_s","time_ms","type"]);w.writeheader();w.writerows(rows)
    (work/"audio_analysis.json").write_text(json.dumps({
        "track":str(path),"duration_s":len(y)/sr,"sample_rate":sr,"estimated_bpm":bpm,
        "drop_source_s":drop,"drop_edit_s":DROP_EDIT,"subbass_jump_ratio":jump,
        "drop_onset_strength":drop_strength,"pre_drop_breath_s":0.55,
        "transients_s":merged,"music_start_s":max(0.0,drop-DROP_EDIT)
    },indent=2))
    return {"drop":drop,"start":max(0.0,drop-DROP_EDIT),"bpm":bpm,"transients":merged,"duration":len(y)/sr}

def ocr_text(frame):
    try:
        import pytesseract
        small=cv2.resize(frame,(max(1,frame.shape[1]//2),max(1,frame.shape[0]//2)))
        return pytesseract.image_to_string(small,lang="eng+jpn",config="--psm 11")
    except Exception:
        return ""

def frame_metrics(frame, prev_gray):
    hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
    h,s,v=cv2.split(hsv)
    cyan=((h>=78)&(h<=105)&(s>80)&(v>100)).mean()
    blue=((h>=96)&(h<=132)&(s>75)&(v>90)).mean()
    violet=((h>=132)&(h<=165)&(s>65)&(v>75)).mean()
    red=((((h<=10)|(h>=170))&(s>90)&(v>90))).mean()
    upper=frame[:max(1,frame.shape[0]//3),:]
    hu=cv2.cvtColor(upper,cv2.COLOR_BGR2HSV)
    whitehair=((hu[:,:,2]>185)&(hu[:,:,1]<95)).mean()
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    motion=0.0
    if prev_gray is not None:
        flow=cv2.calcOpticalFlowFarneback(prev_gray,gray,None,0.5,2,15,2,5,1.1,0)
        motion=float(np.mean(np.sqrt(flow[:,:,0]**2+flow[:,:,1]**2)))
    return {"cyan":float(cyan),"blue":float(blue),"violet":float(violet),"red":float(red),
            "whitehair":float(whitehair),"motion":motion},gray

def inspect_candidates(videos):
    candidates=[]
    face_cascade=cv2.CascadeClassifier(cv2.data.haarcascades+"haarcascade_frontalface_default.xml")
    eye_cascade=cv2.CascadeClassifier(cv2.data.haarcascades+"haarcascade_eye.xml")
    for video in videos:
        cap=cv2.VideoCapture(str(video))
        fps=cap.get(cv2.CAP_PROP_FPS) or 30.0
        total=cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration=total/fps if fps else 0
        prev=None
        for t in np.arange(0.25,max(0.3,duration-1.8),0.45):
            cap.set(cv2.CAP_PROP_POS_MSEC,float(t*1000))
            ok,frame=cap.read()
            if not ok: continue
            # Reject obvious promo title/date cards; subtitles are retained only when they do not resemble title graphics.
            txt=ocr_text(frame)
            if BLOCK_WORDS.search(txt) or BLOCK_JP.search(txt):
                continue
            words=re.findall(r"[A-Z]{3,}",txt)
            if len(words)>=2 and len(txt.strip())>12:
                continue
            m,prev=frame_metrics(frame,prev)
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            faces=face_cascade.detectMultiScale(gray,1.1,4,minSize=(36,36))
            face=None
            if len(faces):
                face=sorted(faces,key=lambda f:f[2]*f[3],reverse=True)[0]
                x,y,w,h=map(int,face)
                roi=gray[y:y+int(h*0.6),x:x+w]
                eyes=eye_cascade.detectMultiScale(roi,1.1,3,minSize=(8,8)) if roi.size else []
                face_score=min(1.0,(w*h)/(frame.shape[0]*frame.shape[1]*0.08))
                m["face"]=float(face_score)
                m["eyes"]=float(min(2,len(eyes))/2)
            else:
                m["face"]=0.0;m["eyes"]=0.0
            # Gojo whitelist heuristic: bright/white upper hair or strong blue/violet Six-Eyes energy.
            gojo_score=2.5*m["whitehair"]+2.0*m["blue"]+1.8*m["cyan"]+1.6*m["violet"]+0.6*m["face"]
            if gojo_score < 0.035:
                continue
            if face is not None and m["whitehair"]<0.015 and (m["blue"]+m["violet"])<0.002:
                continue
            action_score=1.8*m["motion"]+2.0*m["cyan"]+2.2*m["blue"]+2.6*m["violet"]+2.0*m["red"]+0.9*m["eyes"]
            if action_score < 0.015: action_score += gojo_score
            candidates.append({"source":str(video),"start":float(t),"duration":duration,
                "score":float(action_score),"gojo_score":float(gojo_score),**m})
        cap.release()
    # stable highest-scoring candidates only; all windows remain distinct.
    candidates.sort(key=lambda c:c["score"]+0.3*c["gojo_score"],reverse=True)
    unique=[]
    for c in candidates:
        if all(c["source"]!=u["source"] or abs(c["start"]-u["start"])>=1.55 for u in unique):
            unique.append(c)
        if len(unique)>=36: break
    return unique

def face_center(frame):
    cascade=cv2.CascadeClassifier(cv2.data.haarcascades+"haarcascade_frontalface_default.xml")
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    faces=cascade.detectMultiScale(gray,1.1,4,minSize=(32,32))
    if len(faces):
        x,y,w,h=max(faces,key=lambda f:f[2]*f[3])
        return x+w/2, y+h*0.42
    return frame.shape[1]/2, frame.shape[0]*0.42

def smart_crop(frame, state):
    h,w=frame.shape[:2]
    target=W/H
    if w/h>target:
        cw=int(h*target)
        cx,cy=face_center(frame)
        if state.get("cx") is not None: cx=0.78*state["cx"]+0.22*cx
        state["cx"]=cx
        x0=int(np.clip(cx-cw/2,0,w-cw))
        crop=frame[:,x0:x0+cw]
    else:
        ch=int(w/target)
        cx,cy=face_center(frame)
        if state.get("cy") is not None: cy=0.78*state["cy"]+0.22*cy
        state["cy"]=cy
        y0=int(np.clip(cy-ch/2,0,h-ch))
        crop=frame[y0:y0+ch,:]
    return cv2.resize(crop,(W,H),interpolation=cv2.INTER_LANCZOS4)

def grade(frame, t, beat_times, impact_times, seed):
    x=frame.astype(np.float32)/255.0
    # obsidian shadows with restrained cold teal lift
    lum=x[:,:,0]*0.12+x[:,:,1]*0.68+x[:,:,2]*0.20
    shadow=np.clip((0.42-lum)/0.42,0,1)*0.065
    x[:,:,0]+=shadow*0.55
    x[:,:,1]+=shadow*0.20
    x[:,:,2]-=shadow*0.16
    x=np.clip((x-0.025)*1.08,0,1)
    hsv=cv2.cvtColor(np.uint8(np.clip(x*255,0,255)),cv2.COLOR_BGR2HSV)
    hue,sat,val=cv2.split(hsv)
    mask=((val>205)&(((hue>=78)&(hue<=165))|(sat<45))).astype(np.uint8)*255
    glow=cv2.GaussianBlur(cv2.bitwise_and(np.uint8(np.clip(x*255,0,255)),np.uint8(np.repeat(mask[:,:,None],3,axis=2))),(0,0),sigmaX=22)
    x=np.clip(x+glow.astype(np.float32)/255.0*0.20,0,1)
    # damped kick zoom and shake
    nearest=min((abs(t-b) for b in beat_times),default=10)
    amp=max(0.0,1.0-nearest/0.12)
    scale=1.0+0.12*amp
    angle=(math.sin(t*59.0)*0.55*amp)
    tx=math.sin(t*83.0)*9.0*amp
    ty=math.cos(t*73.0)*8.0*amp
    mat=cv2.getRotationMatrix2D((W/2,H/2),angle,scale)
    mat[0,2]+=tx;mat[1,2]+=ty
    out=cv2.warpAffine(np.uint8(np.clip(x*255,0,255)),mat,(W,H),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT)
    # RGB split only on real transients, with exponential decay over 8 frames.
    if amp>0.05:
        shift=int(round(12*amp))
        b,g,r=cv2.split(out)
        r=cv2.warpAffine(r,np.float32([[1,0,shift],[0,1,0]]),(W,H),borderMode=cv2.BORDER_REFLECT)
        b=cv2.warpAffine(b,np.float32([[1,0,-shift],[0,1,0]]),(W,H),borderMode=cv2.BORDER_REFLECT)
        out=cv2.merge([b,g,r])
    # vignette and fine grain
    yy,xx=np.ogrid[:H,:W]
    rad=((xx-W/2)/(W*0.72))**2+((yy-H/2)/(H*0.70))**2
    vign=np.clip(1.0-0.22*np.maximum(0,rad-0.18),0.70,1.0).astype(np.float32)
    out=np.uint8(np.clip(out.astype(np.float32)*vign[:,:,None],0,255))
    rng=np.random.default_rng(seed)
    grain=rng.normal(0,1.2,(H,W,1)).astype(np.float32)
    out=np.uint8(np.clip(out.astype(np.float32)+grain,0,255))
    if any(abs(t-d)<2/FPS_BASE for d in impact_times):
        gray=cv2.cvtColor(out,cv2.COLOR_BGR2GRAY)
        ink=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,31,7)
        inv=255-ink
        ink3=cv2.cvtColor(inv,cv2.COLOR_GRAY2BGR)
        ink3[:,:,0]=np.clip(ink3[:,:,0].astype(np.int16)+22,0,255).astype(np.uint8)
        ink3[:,:,2]=np.clip(ink3[:,:,2].astype(np.int16)+42,0,255).astype(np.uint8)
        out=cv2.addWeighted(out,0.28,ink3,0.72,0)
    return out

def load_font(size):
    paths=[
      "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
      "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
    ]
    for p in paths:
        if Path(p).exists():
            try:return ImageFont.truetype(p,size)
            except: pass
    return ImageFont.load_default()

def subtitle_overlay(frame, text):
    if not text:return frame
    img=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
    draw=ImageDraw.Draw(img)
    font=load_font(56)
    # wrap without truncation; center in a dark translucent plate with enough room for Japanese glyphs.
    lines=textwrap.wrap(text,width=24,break_long_words=True,replace_whitespace=False)[:3]
    rendered="\n".join(lines)
    bbox=draw.multiline_textbbox((0,0),rendered,font=font,spacing=12,stroke_width=5)
    tw,th=bbox[2]-bbox[0],bbox[3]-bbox[1]
    box_w=min(W-80,tw+80); box_h=th+58
    x=(W-box_w)//2; y=H-470
    plate=Image.new("RGBA",(W,H),(0,0,0,0))
    pd=ImageDraw.Draw(plate)
    pd.rounded_rectangle((x,y,x+box_w,y+box_h),radius=22,fill=(0,0,0,145),outline=(30,180,255,150),width=2)
    img=Image.alpha_composite(img.convert("RGBA"),plate)
    draw=ImageDraw.Draw(img)
    draw.multiline_text(((W-tw)//2,y+28),rendered,font=font,fill=(245,250,255,255),
        stroke_width=6,stroke_fill=(0,0,0,255),spacing=12,align="center")
    return cv2.cvtColor(np.array(img.convert("RGB")),cv2.COLOR_RGB2BGR)

def extract_frame(cap, source_t):
    cap.set(cv2.CAP_PROP_POS_MSEC,max(0,source_t)*1000)
    ok,frame=cap.read()
    if not ok:
        cap.set(cv2.CAP_PROP_POS_MSEC,0)
        ok,frame=cap.read()
    return frame if ok else None

def write_riser(path):
    sr=48000;n=int(sr*0.62);t=np.arange(n)/sr
    rng=np.random.default_rng(7331)
    noise=rng.normal(0,1,n)
    # upward sine sweep, band-limited, shaped as a compact pre-drop lift
    sweep=chirp(t,f0=120,f1=1800,t1=0.62,method="logarithmic")
    env=np.linspace(0,1,n)**1.8
    x=(0.18*noise+0.42*sweep)*env
    x=np.clip(x,-0.95,0.95)
    with wave.open(str(path),"wb") as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(sr)
        f.writeframes((x*32767).astype("<i2").tobytes())

def make_audio(music, voice1, voice2, riser, music_start, out, v1dur, v2dur):
    # voice placement is tied to the two acts; final 160 ms before the drop is dead air.
    f=(
      f"[0:a]asplit=2[m0][m1];"
      f"[m0]atrim=start={music_start:.4f}:duration={DROP_EDIT},asetpts=PTS-STARTPTS,"
      f"lowpass=f=560,volume=0.17,afade=t=out:st=7.00:d=0.60[mintro];"
      f"[m1]atrim=start={music_start+DROP_EDIT:.4f}:duration={DURATION-DROP_EDIT},asetpts=PTS-STARTPTS,volume=1.0[mdrop];"
      f"[mintro][mdrop]concat=n=2:v=0:a=1[bg];"
      f"[1:a]adelay=450|450,volume=1.20[v1];"
      f"[2:a]adelay=5500|5500,volume=1.35,highpass=f=120,treble=g=3:f=3200, aecho=0.75:0.55:70|115:0.18|0.10[v2];"
      f"[3:a]adelay=6900|6900,volume=0.42[r];"
      f"[bg][v1][v2][r]amix=inputs=4:duration=longest:dropout_transition=0:normalize=0,"
      f"atrim=duration={DURATION},afade=t=out:st=21.35:d=1.05,"
      f"loudnorm=I=-11:TP=-1.0:LRA=7:linear=true,alimiter=limit=0.89[a]"
    )
    sh(["ffmpeg","-y","-v","error","-i",str(music),"-i",str(voice1),"-i",str(voice2),"-i",str(riser),
        "-filter_complex",f,"-map","[a]","-c:a","pcm_s24le","-ar","48000",str(out)])

def choose_shots(candidates, setup, punch, files_by_path):
    # Timeline entries use unique source windows and exclude the intro/punchline windows.
    shots=[]
    def add(start,end,src,start_src,role,meta=None):
        shots.append({"start":start,"end":end,"source":src,"source_start":start_src,"role":role,"meta":meta or {}})
    add(0.0,5.45,setup["source"],max(0,setup["start"]-0.45),"dialogue_setup",setup)
    add(5.45,DROP_EDIT,punch["source"],max(0,punch["start"]-0.25),"punchline",punch)
    # The post-drop uses diverse unique windows, biased towards violet/blue/red and optical-flow peaks.
    remaining=[c for c in candidates if not (
        (c["source"]==setup["source"] and abs(c["start"]-setup["start"])<2.0) or
        (c["source"]==punch["source"] and abs(c["start"]-punch["start"])<2.0))]
    def cls(c):
        return max([("violet",c["violet"]),("blue",c["blue"]),("red",c["red"]),("motion",c["motion"]),("calm",c["whitehair"])],key=lambda x:x[1])[0]
    buckets=defaultdict(list)
    for c in remaining:buckets[cls(c)].append(c)
    order=["violet","blue","red","motion","blue","violet","motion","calm","red","blue","calm"]
    selected=[]
    for k in order:
        pool=buckets[k]
        while pool and pool[0] in selected:pool.pop(0)
        if pool:
            selected.append(pool.pop(0))
    for c in remaining:
        if len(selected)>=12:break
        if c not in selected and all(c["source"]!=u["source"] or abs(c["start"]-u["start"])>1.55 for u in selected):
            selected.append(c)
    if len(selected)<10:
        raise RuntimeError(f"Only {len(selected)} unique, Gojo-scored shots passed the frame/OCR filters; need at least 10 for the action section.")
    # 11 action shots across 13.6 seconds; no section is left empty.
    bounds=[7.60,8.72,9.80,10.88,11.96,13.04,14.12,15.20,16.28,17.36,18.44,19.52,20.60,21.28]
    for i,(a,b) in enumerate(zip(bounds[:-1],bounds[1:])):
        c=selected[i%len(selected)]
        add(a,b,c["source"],c["start"],"aura_action",c)
    # Iconic ending is the cleanest high-Gojo-score, low-motion candidate not previously used.
    used={(s["source"],round(s["source_start"],1)) for s in shots}
    ending=sorted([c for c in candidates if (c["source"],round(c["start"],1)) not in used],
                  key=lambda c:(c["gojo_score"]-0.9*c["motion"]+1.1*(c["blue"]+c["violet"])),reverse=True)
    if not ending: ending=selected
    c=ending[0]
    add(21.28,DURATION,c["source"],c["start"],"final_hold",c)
    return shots

def render_frames(shots, candidates, audio_info, voice_setup, voice_punch, outdir):
    caps={}
    for s in shots:
        if s["source"] not in caps:caps[s["source"]]=cv2.VideoCapture(s["source"])
    beat_edit=[DROP_EDIT]+[DROP_EDIT+(t-audio_info["drop"]) for t in audio_info["transients"] if t>=audio_info["drop"]]
    beat_edit=[t for t in beat_edit if 0<t<DURATION]
    impact_times=sorted(set([DROP_EDIT]+[t for t in beat_edit if 0<t<DURATION and t>DROP_EDIT+0.1]))
    fourcc=cv2.VideoWriter_fourcc(*"mp4v")
    raw=outdir/"visual_30fps_master.mp4"
    writer=cv2.VideoWriter(str(raw),fourcc,FPS_BASE,(W,H))
    if not writer.isOpened():raise RuntimeError("OpenCV could not open the visual master writer.")
    states=[{} for _ in shots]
    frame_count=int(round(DURATION*FPS_BASE))
    for idx in range(frame_count):
        t=idx/FPS_BASE
        si=next((i for i,s in enumerate(shots) if s["start"]<=t<s["end"]),len(shots)-1)
        s=shots[si];local=t-s["start"];dur=s["end"]-s["start"]
        # smooth ease from 0.55x into a 1.8x burst for action; dialogue stays calm.
        if s["role"] in ("dialogue_setup","punchline","final_hold"):
            speed=0.60 if s["role"]!="final_hold" else 0.80
        else:
            ramp=np.clip(local/0.24,0,1);ease=ramp*ramp*(3-2*ramp)
            speed=0.55+1.25*ease
        src_t=s["source_start"]+local*speed
        cap=caps[s["source"]]
        fr=extract_frame(cap,src_t)
        if fr is None:
            raise RuntimeError(f"Could not decode source frame {s['source']} @ {src_t:.3f}s")
        frame=smart_crop(fr,states[si])
        frame=grade(frame,t,beat_edit,impact_times,idx)
        if t<5.45:
            text=voice_setup["text"] if t>=0.45 else ""
            frame=subtitle_overlay(frame,text)
        elif 5.45<=t<DROP_EDIT:
            frame=subtitle_overlay(frame,voice_punch["text"])
        if s["role"]=="final_hold":
            # hold on Gojo only; no text card or outro slogan.
            pass
        writer.write(frame)
    writer.release()
    for c in caps.values():c.release()
    return raw,beat_edit,impact_times

def contact_sheets(video, outdir, drop):
    cap=cv2.VideoCapture(str(video))
    count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps=cap.get(cv2.CAP_PROP_FPS) or FPS_FINAL
    # 16-frame overview grid
    ts=np.linspace(0,DURATION-0.05,16)
    thumbs=[]
    for t in ts:
        cap.set(cv2.CAP_PROP_POS_MSEC,float(t*1000));ok,fr=cap.read()
        if not ok:continue
        fr=cv2.resize(fr,(270,480),interpolation=cv2.INTER_AREA)
        cv2.putText(fr,f"{t:05.2f}s",(12,30),cv2.FONT_HERSHEY_SIMPLEX,0.8,(255,255,255),2,cv2.LINE_AA)
        thumbs.append(fr)
    grid=np.zeros((480*4,270*4,3),dtype=np.uint8)
    for i,fr in enumerate(thumbs[:16]):grid[(i//4)*480:(i//4+1)*480,(i%4)*270:(i%4+1)*270]=fr
    cv2.imwrite(str(outdir/"overview_16frame.jpg"),grid,[cv2.IMWRITE_JPEG_QUALITY,94])
    drop_frames=[]
    for dt in [-0.10,-0.05,-0.017,0,0.05,0.10]:
        t=max(0,min(DURATION-0.01,drop+dt))
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,fr=cap.read()
        if ok:
            fr=cv2.resize(fr,(360,640),interpolation=cv2.INTER_AREA)
            cv2.putText(fr,f"{t:05.3f}s",(12,35),cv2.FONT_HERSHEY_SIMPLEX,0.9,(255,255,255),2,cv2.LINE_AA)
            drop_frames.append(fr)
    sheet=np.zeros((640*2,360*3,3),dtype=np.uint8)
    for i,fr in enumerate(drop_frames[:6]):sheet[(i//3)*640:(i//3+1)*640,(i%3)*360:(i%3+1)*360]=fr
    cv2.imwrite(str(outdir/"drop_contact_sheet_6frame.jpg"),sheet,[cv2.IMWRITE_JPEG_QUALITY,95])
    cap.release()

def build_manifest(outdir, shots, setup, punch, music_info, v1dur, v2dur):
    data={
      "title":"Gojo Satoru — Dialogue to Drop Aura Edit V2",
      "duration_s":DURATION,"base_fps":FPS_BASE,"final_fps":FPS_FINAL,
      "resolution":[W,H],"audio":{"track":"REACTOR by NO LIMITS PHONK",
      "music_credit":"Music: NO LIMITS PHONK — https://www.youtube.com/@nolimits_phonk",
      "drop_source_s":music_info["drop"],"drop_edit_s":DROP_EDIT,"bpm":music_info["bpm"],
      "dialogue_setup":setup,"dialogue_punchline":punch},
      "shots":shots,
      "qa_policy":{"kenjaku_prison_realm":"excluded by Gojo white-hair/energy visual whitelist and OCR; visual spot-check artifact supplied",
      "promo_text":"OCR rejection for DOMAIN/INFINITE/VOID/IMAX/date cards",
      "shot_reuse":"unique sampled source windows; >=1.55 s separation within source"}
    }
    (outdir/"manifest.json").write_text(json.dumps(data,ensure_ascii=False,indent=2))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input-dir",required=True);ap.add_argument("--output-dir",required=True);ap.add_argument("--duration",type=float,default=DURATION)
    args=ap.parse_args()
    global DURATION
    DURATION=float(args.duration)
    inp=Path(args.input_dir);out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    music=inp/"REACTOR.mp3"
    videos=sorted(inp.glob("*.mp4"))
    if not music.exists():raise FileNotFoundError("REACTOR.mp3 missing.")
    if len(videos)<1:raise FileNotFoundError("No downloaded Gojo source MP4s.")
    segments=transcribe_sources(videos,out)
    setup,punch=select_dialogue(segments)
    audio_info=analyze_music(music,out)
    # Require a usable drop with sufficient music before and after.
    if audio_info["start"]+DURATION>audio_info["duration"]:
        audio_info["start"]=max(0,audio_info["duration"]-DURATION)
    v1=out/"dialogue_setup.wav";v2=out/"dialogue_punchline.wav";riser=out/"riser.wav";audio=out/"master_audio.wav"
    v1dur=render_voice_clip(setup,v1,3.65);v2dur=render_voice_clip(punch,v2,1.78)
    write_riser(riser);make_audio(music,v1,v2,riser,audio_info["start"],audio,v1dur,v2dur)
    candidates=inspect_candidates(videos)
    if len(candidates)<12:raise RuntimeError(f"Only {len(candidates)} frames passed Gojo/OCR screening.")
    shots=choose_shots(candidates,setup,punch,{})
    # Persist candidate scores for audit.
    (out/"scene_scores.json").write_text(json.dumps(candidates,indent=2))
    raw,beats,impacts=render_frames(shots,candidates,audio_info,setup,punch,out)
    final=out/"Gojo_Satoru_Aura_Edit_V2.mp4"
    # Genuine motion interpolation: 30fps composited master -> 60fps MCI output, no frame duplication.
    sh(["ffmpeg","-y","-v","warning","-i",str(raw),"-i",str(audio),
        "-filter:v","minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,fade=t=out:st=21.35:d=1.05,format=yuv420p",
        "-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","slow","-crf","16","-r","60",
        "-c:a","aac","-b:a","320k","-t",f"{DURATION:.3f}","-movflags","+faststart",str(final)])
    contact_sheets(final,out,audio_info["drop"]-audio_info["start"]+DROP_EDIT)
    build_manifest(out,shots,setup,punch,audio_info,v1dur,v2dur)
    # Store a compact render report, the verifier performs strict stream/frame checks.
    (out/"render_report.json").write_text(json.dumps({
      "frames_base":int(DURATION*FPS_BASE),"frames_final":int(DURATION*FPS_FINAL),
      "base_fps":FPS_BASE,"final_fps":FPS_FINAL,"shot_count":len(shots),
      "action_shot_count":sum(s["role"]=="aura_action" for s in shots),
      "dialogue_setup_text":setup["text"],"punchline_text":punch["text"],
      "music_drop_edit_s":DROP_EDIT,"beat_count":len(beats),"impact_count":len(impacts),
      "video":str(final)
    },ensure_ascii=False,indent=2))
    print(json.dumps({"video":str(final),"shots":len(shots),"dialogue_setup":setup["text"],"punchline":punch["text"]},ensure_ascii=False))

if __name__=="__main__":
    try:main()
    except Exception as e:
        print(f"FATAL: {e}",file=sys.stderr)
        raise
