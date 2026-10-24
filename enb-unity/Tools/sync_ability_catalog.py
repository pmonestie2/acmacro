"""Copy public supported ability labels from the Rust server into a Unity resource."""

import json
from pathlib import Path


def main() -> None:
    """Refresh the display catalog while keeping server packets authoritative."""
    root = Path(__file__).resolve().parents[2]
    classes = json.loads((root / "enb-server/data/class_skills.json").read_text())
    abilities = {}
    for character_class in classes:
        for ability in character_class["abilities"]:
            if ability["kind"] == "unsupported":
                continue
            key = ability["id"]
            display = {
                "id": key,
                "skill": ability["skill"],
                "name": ability["name"],
                "description": ability["description"],
                "kind": ability["kind"],
                "rank": ability["rank"],
                "energy": ability["energy"],
                "charge": ability["charge"],
                "cooldown": ability["cooldown"],
                "range": ability["range"],
            }
            if key in abilities and abilities[key] != display:
                raise ValueError(f"Conflicting public ability definition for {key}")
            abilities[key] = display
    target = root / "enb-unity/Assets/Enb/Resources/AbilityCatalog.txt"
    fields = ("id", "skill", "name", "description", "kind", "rank", "energy", "charge", "cooldown", "range")
    rows = []
    for key in sorted(abilities):
        rows.append("\t".join(str(abilities[key][field]).replace("\t", " ").replace("\n", " ")
                              for field in fields))
    target.write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
