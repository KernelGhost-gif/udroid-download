#!/usr/bin/env python3
import json, re, subprocess, sys
from pathlib import Path
import cv2
import numpy as np

OUT=Path(sys.argv[1])
VIDEO=OUT/"Gojo_Satoru_Aura_Edit_V2.mp4"

def run(args):
    p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError(p.stderr[-3000:])
    return p.stdout,p.stderr

raw,_=run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(VIDEO)])
probe=json.loads(raw)
v=next(s for s in probe["streams"] if s["codec_type"]=="video")
a=next(s for s in probe["streams"] if s["codec_type"]=="audio")
fps=v.get("avg_frame_rate","0/1")
num,den=map(int,fps.split("/"))
fpsval=num/den if den else 0
duration=float(probe["format"]["duration"])
checks={
 "video_codec_h264":v.get("codec_name")=="h264",
 "audio_codec_aac":a.get("codec_name")=="aac",
 "resolution_1080x1920":(v.get("width"),v.get("height"))==(1080,1920),
 "fps_60":abs(fpsval-60.0)<0.01,
 "duration_22_4":abs(duration-22.4)<0.12,
 "pixel_format_yuv420p":v.get("pix_fmt")=="yuv420p",
 "audio_320k":int(a.get("bit_rate",0) or 0)>=290000,
}
# Verify file decodes and sample frames aren't unintentionally black before the end fade.
cap=cv2.VideoCapture(str(VIDEO))
if not cap.isOpened(): raise RuntimeError("Final MP4 cannot be opened by OpenCV.")
means=[]; diffs=[]; prev=None
for t in np.arange(0.15,21.25,0.40):
    cap.set(cv2.CAP_PROP_POS_MSEC,float(t*1000))
    ok,fr=cap.read()
    if not ok: continue
    gray=cv2.cvtColor(cv2.resize(fr,(180,320)),cv2.COLOR_BGR2GRAY)
    means.append(float(gray.mean()))
    if prev is not None: diffs.append(float(np.mean(cv2.absdiff(gray,prev))))
    prev=gray
cap.release()
checks["no_unintended_black_samples"]=len(means)>=35 and min(means)>4.0
checks["visual_samples_decode"]=len(means)>=35
checks["not_entirely_static"]=len(diffs)>0 and np.percentile(diffs,75)>0.25
# Check the explicitly banned theatrical title-card tokens in sampled frames.
ocr_bad=[]
try:
    import pytesseract
    cap=cv2.VideoCapture(str(VIDEO))
    for t in np.arange(0.5,21.0,0.7):
        cap.set(cv2.CAP_PROP_POS_MSEC,float(t*1000));ok,fr=cap.read()
        if not ok:continue
        small=cv2.resize(fr,(540,960))
        txt=pytesseract.image_to_string(small,lang="eng+jpn",config="--psm 11")
        if re.search(r"\b(IMAX|DECEMBER|THEATRICAL|TICKETS|RELEASE DATE|COMING SOON)\b",txt,re.I):
            ocr_bad.append({"time_s":round(float(t),2),"text":txt.strip()[:160]})
    cap.release()
except Exception as e:
    ocr_bad=[{"warning":"OCR verification unavailable","detail":str(e)}]
checks["no_burned_in_promo_text"]=len(ocr_bad)==0
# Confirm the render produced the complete requested QA bundle.
required=["overview_16frame.jpg","drop_contact_sheet_6frame.jpg","beat_timing.csv",
          "audio_analysis.json","manifest.json","render_report.json","transcript.json"]
checks["qa_artifacts_present"]=all((OUT/n).exists() and (OUT/n).stat().st_size>0 for n in required)
report={
 "video":str(VIDEO),"file_bytes":VIDEO.stat().st_size,
 "format":probe["format"],"video_stream":v,"audio_stream":a,
 "sampled_mean_luma_min":min(means) if means else None,
 "sampled_frame_count":len(means),"sampled_frame_diff_p75":float(np.percentile(diffs,75)) if diffs else None,
 "promo_ocr_hits":ocr_bad,"checks":checks,"passed":all(checks.values())
}
(OUT/"qa_report.json").write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
if not report["passed"]:
    raise SystemExit("QA GATE FAILED")
