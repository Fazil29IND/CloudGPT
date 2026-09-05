import json
import csv
import re
from pathlib import Path

def generate_id(provider: str, service_name: str) -> str:
    # simplify service name
    clean_name = re.sub(r'\s*\(.*?\)', '', service_name)
    clean_name = re.sub(r'[^a-zA-Z0-9]+', '-', clean_name.lower()).strip('-')
    if not clean_name:
        clean_name = re.sub(r'[^a-zA-Z0-9]+', '-', service_name.lower()).strip('-')
    provider_prefix = provider.lower().replace(' ', '-')
    return f"{provider_prefix}-{clean_name}"


def split_cell_services(text: str) -> list[str]:
    if not text or text.strip() == '-' or text.strip().lower() in ('none', 'n/a'):
        return []

    tokens = []
    current = []
    paren_depth = 0

    for ch in text:
        if ch == '(':
            paren_depth += 1
            current.append(ch)
        elif ch == ')':
            paren_depth = max(0, paren_depth - 1)
            current.append(ch)
        elif paren_depth == 0 and (ch == ',' or ch == ';' or ch == '/'):
            token = ''.join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(ch)

    token = ''.join(current).strip()
    if token:
        tokens.append(token)

    cleaned = []
    for tok in tokens:
        tok = tok.strip().strip('*').strip()
        if ' + ' in tok and '(' not in tok:
            for sub in tok.split(' + '):
                sub = sub.strip()
                if sub:
                    cleaned.append(sub)
        else:
            if tok:
                cleaned.append(tok)

    return cleaned


def extract_status_and_aliases(service_str: str, aliases_map: dict[str, str]) -> tuple[str, str, list[str]]:
    service_status = "active"
    aliases_list = []

    lower = service_str.lower()
    if "retired" in lower or "retirement" in lower:
        service_status = "retired"
    elif "deprecated" in lower or "eol" in lower:
        service_status = "deprecated"
    elif "maintenance" in lower:
        service_status = "maintenance"
    elif "preview" in lower:
        service_status = "preview"

    # Extract parenthesized former names or notes
    # e.g., 'Deployments (formerly Agent Engine)'
    match_formerly = re.search(r'\(formerly\s+([^)]+)\)', service_str, re.IGNORECASE)
    if match_formerly:
        aliases_list.append(match_formerly.group(1).strip())

    # e.g., 'Amazon Kiro (Amazon Q Developer sunset EOL April 2027)'
    match_sunset = re.search(r'\(([^)]+)\s+sunset\s+EOL[^)]*\)', service_str, re.IGNORECASE)
    if match_sunset:
        aliases_list.append(match_sunset.group(1).strip())

    # e.g., 'Search (formerly Vertex AI Search)'
    match_gen = re.search(r'\(([^)]+)\)', service_str)
    if match_gen and not match_formerly and not match_sunset:
        note = match_gen.group(1).strip()
        if any(w in note.lower() for w in ["formerly", "replace", "deprecated", "sunset"]):
            aliases_list.append(note)

    # Check aliases.json map
    for k, v in aliases_map.items():
        if k.lower() in service_str.lower():
            if "(retired" in v or "(deprecated" in v or "(in retirement" in v:
                if service_status == "active":
                    service_status = "deprecated" if "deprecated" in v else "retired"
            elif v not in aliases_list:
                aliases_list.append(v)

    # Clean display name
    clean_name = service_str.strip()
    return clean_name, service_status, aliases_list


def get_doc_url(provider: str, service_name: str) -> str:
    clean_slug = re.sub(r'\s*\(.*?\)', '', service_name)
    clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', clean_slug.lower()).strip('-')
    if not clean_slug:
        clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', service_name.lower()).strip('-')

    if provider == "AWS":
        return f"https://docs.aws.amazon.com/{clean_slug}/"
    elif provider == "Google Cloud":
        return f"https://cloud.google.com/{clean_slug}/docs"
    elif provider == "Azure":
        return f"https://learn.microsoft.com/en-us/azure/{clean_slug}/"
    return ""


def main():
    base_dir = Path(__file__).resolve().parent.parent
    sources_dir = base_dir / "sources"
    md_path = base_dir / "Services.Md"
    aliases_path = sources_dir / "aliases.json"

    aliases_map = {}
    if aliases_path.exists():
        with open(aliases_path, 'r', encoding='utf-8') as f:
            aliases_map = json.load(f)

    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()

    manifest: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()  # (provider, category, service_name)

    current_section = ""
    current_category = ""

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("## "):
            current_section = line.replace("## ", "").strip()
            continue

        if line.startswith("### "):
            current_category = line.replace("### ", "").strip()
            continue

        if line.startswith("|") and not line.startswith("| :---") and not line.startswith("| Phase") and not line.startswith("| Purpose"):
            cols = [c.strip() for c in line.split("|")[1:-1]]

            # 1. Lifecycle Table: | Phase | Description | AWS | Google Cloud | Azure |
            if "Lifecycle" in current_section and len(cols) >= 5:
                phase_title = cols[0].replace("**", "").strip()
                phase_desc = cols[1]
                category_name = f"Lifecycle: {phase_title}"

                provider_cols = [
                    ("AWS", cols[2]),
                    ("Google Cloud", cols[3]),
                    ("Azure", cols[4]),
                ]

                for provider, cell_text in provider_cols:
                    services = split_cell_services(cell_text)
                    for svc_str in services:
                        display_name, status, aliases = extract_status_and_aliases(svc_str, aliases_map)
                        key = (provider, category_name, display_name.lower())
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)

                        entry = {
                            "id": generate_id(provider, display_name),
                            "provider": provider,
                            "category": category_name,
                            "purpose": phase_desc,
                            "service": display_name,
                            "display_name": display_name,
                            "urls": [
                                {
                                    "url": get_doc_url(provider, display_name),
                                    "source_type": "official_docs",
                                    "priority": 1
                                }
                            ],
                            "service_status": status,
                            "aliases": aliases
                        }
                        manifest.append(entry)

            # 2. Categorized Catalog Tables: | Purpose | AWS | Google Cloud | Azure |
            elif "Categorized" in current_section and len(cols) >= 4:
                purpose = cols[0]
                category_name = current_category or "General"

                provider_cols = [
                    ("AWS", cols[1]),
                    ("Google Cloud", cols[2]),
                    ("Azure", cols[3]),
                ]

                for provider, cell_text in provider_cols:
                    services = split_cell_services(cell_text)
                    for svc_str in services:
                        display_name, status, aliases = extract_status_and_aliases(svc_str, aliases_map)
                        key = (provider, category_name, display_name.lower())
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)

                        entry = {
                            "id": generate_id(provider, display_name),
                            "provider": provider,
                            "category": category_name,
                            "purpose": purpose,
                            "service": display_name,
                            "display_name": display_name,
                            "urls": [
                                {
                                    "url": get_doc_url(provider, display_name),
                                    "source_type": "official_docs",
                                    "priority": 1
                                }
                            ],
                            "service_status": status,
                            "aliases": aliases
                        }
                        manifest.append(entry)

    # Write JSON
    json_out = sources_dir / "sources.json"
    with open(json_out, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    # Write CSV
    csv_out = sources_dir / "sources.csv"
    if manifest:
        keys = ["id", "provider", "category", "purpose", "service", "display_name", "url", "service_status", "aliases"]
        with open(csv_out, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(keys)
            for m in manifest:
                writer.writerow([
                    m["id"],
                    m["provider"],
                    m["category"],
                    m.get("purpose", ""),
                    m["service"],
                    m["display_name"],
                    m["urls"][0]["url"] if m["urls"] else "",
                    m["service_status"],
                    "|".join(m["aliases"])
                ])

    print(f"Successfully generated {json_out} and {csv_out} with {len(manifest)} services.")


if __name__ == "__main__":
    main()
