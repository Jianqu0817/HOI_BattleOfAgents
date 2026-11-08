"""Install the pinned upstream CPU inference API in a separate directory/venv."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import urllib.request
import zipfile

from start_gpt_sovits import REVISION


MODEL_REVISION = "8f8857563c040d542de108a2c18b0425abd6c9c5"
EXTRA_REVISION = "0c47645e02a7bc3688d7b263b0042c81e3cd82cd"
FFMPEG_REVISION = "e6d0c1a17da07c33557852f9dfa2bd44cc75737d"
INFERENCE_DEPENDENCIES = ["numpy<2", "scipy", "librosa==0.10.2", "resampy==0.4.3", "pytorch-lightning>=2.4", "ffmpeg-python",
    "onnxruntime", "tqdm", "cn2an", "pypinyin", "pyopenjtalk", "g2p_en", "sentencepiece",
    "transformers==4.51.3", "peft<0.18", "chardet", "PyYAML", "psutil", "jieba_fast", "jieba",
    "split-lang", "fast_langdetect", "wordsegment", "rotary_embedding_torch", "ToJyutping", "g2pk2",
    "ko_pron", "opencc", "fastapi[standard]>=0.115.2", "x_transformers", "torchmetrics<=1.5",
    "pydantic<=2.10.6", "huggingface_hub", "soundfile", "matplotlib", "tensorboard"]
MODEL_FILES = ["chinese-hubert-base/config.json", "chinese-hubert-base/preprocessor_config.json",
    "chinese-hubert-base/pytorch_model.bin", "chinese-roberta-wwm-ext-large/config.json",
    "chinese-roberta-wwm-ext-large/pytorch_model.bin", "chinese-roberta-wwm-ext-large/tokenizer.json",
    "s1v3.ckpt", "v2Pro/s2Gv2ProPlus.pth", "sv/pretrained_eres2netv2w24s4ep4.ckpt"]


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return
    temporary = target.with_name(target.name + ".part")
    urllib.request.urlretrieve(url, temporary)
    temporary.replace(target)


def setup(root):
    root = Path(root).resolve()
    if not root.exists():
        subprocess.run(["git", "clone", "https://github.com/RVC-Boss/GPT-SoVITS.git", str(root)], check=True)
        subprocess.run(["git", "checkout", REVISION], cwd=root, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if revision != REVISION:
        raise RuntimeError("Existing directory is a different revision; it has not been modified: " + str(root))
    python = root / ".venv/Scripts/python.exe"
    if not python.is_file():
        subprocess.run(["uv", "venv", "--python", "3.11", str(root / ".venv")], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(python), "torch==2.7.1", "torchaudio==2.7.1",
                    "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(python), *INFERENCE_DEPENDENCIES], check=True)
    downloads = [(f"https://huggingface.co/lj1995/GPT-SoVITS/resolve/{MODEL_REVISION}/{name}",
                  root / "GPT_SoVITS/pretrained_models" / name) for name in MODEL_FILES]
    downloads += [(f"https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/{FFMPEG_REVISION}/{name}",
                   root / "runtime" / name) for name in ("ffmpeg.exe", "ffprobe.exe")]
    downloads.append((f"https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/{EXTRA_REVISION}/G2PWModel.zip",
                      root / "G2PWModel.zip"))
    downloads.append(("https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin",
                      root / "GPT_SoVITS/pretrained_models/fast_langdetect/lid.176.bin"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda item: download(*item), downloads))
    if not (root / "GPT_SoVITS/text/G2PWModel/g2pW.onnx").is_file():
        with zipfile.ZipFile(root / "G2PWModel.zip") as archive:
            archive.extractall(root / "GPT_SoVITS/text")
    config_script = (
        "from pathlib import Path; import yaml; p=Path('GPT_SoVITS/configs/tts_infer.yaml'); "
        "v=yaml.safe_load(p.read_text()); v['custom']=dict(v['v2ProPlus']); "
        "v['custom'].update(device='cpu',is_half=False); "
        "Path('iron-tts-cpu.yaml').write_text(yaml.safe_dump(v,sort_keys=False))")
    subprocess.run([str(python), "-c", config_script], cwd=root, check=True)
    with (root / "iron-inference-freeze.txt").open("w", encoding="utf-8") as output:
        subprocess.run(["uv", "pip", "freeze", "--python", str(python)], stdout=output, check=True)
    (root / "iron-deployment-source.json").write_text(json.dumps({"repository": "https://github.com/RVC-Boss/GPT-SoVITS",
        "revision": REVISION, "models_revision": MODEL_REVISION, "extra_revision": EXTRA_REVISION,
        "ffmpeg_revision": FFMPEG_REVISION, "device": "cpu", "api_version": "api_v2"}, indent=2), encoding="utf-8")
    return {"status": "installed", "root": str(root), "revision": REVISION}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="D:/GPT-SoVITS_IRON")
    args = parser.parse_args()
    print(json.dumps(setup(args.root), ensure_ascii=False, indent=2))
