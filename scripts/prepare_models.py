"""Explicit online preparation only; application inference is strictly local."""
import argparse
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/models')
    parser.add_argument('--embedding', default='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
    parser.add_argument('--qa', default='Qwen/Qwen2.5-1.5B-Instruct')
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for kind, repo in [('embedding', args.embedding), ('qa', args.qa)]:
        revision = HfApi().model_info(repo).sha
        snapshot_download(repo, revision=revision, local_dir=root / kind,
            allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model', '*.jinja', '*/config.json'],
            max_workers=2)
        manifest[kind] = {'repo': repo, 'revision': revision, 'path': str((root / kind).resolve())}
        (root / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        print(f'{kind}: {repo}@{revision}', flush=True)


if __name__ == '__main__':
    main()
