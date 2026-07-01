with open("broker/base.py", "r") as f:
    content = f.read()

if "is_gtt: bool = False" not in content:
    content = content.replace(
        "    tag:         str = \"\"             # Optional metadata tag",
        "    tag:         str = \"\"             # Optional metadata tag\n    is_gtt:      bool = False         # Place as GTT order\n    gtt_trigger_price: float = 0.0    # Trigger price for GTT"
    )
    with open("broker/base.py", "w") as f:
        f.write(content)
