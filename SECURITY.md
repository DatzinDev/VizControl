# Security policy

## Supported versions

VizControl is in alpha: only the latest release receives security fixes.

## Reporting a vulnerability

Please report vulnerabilities **privately**, never in a public issue:

- GitHub: [Report a vulnerability](https://github.com/DatzinDev/VizControl/security/advisories/new) (private
  vulnerability reporting), or
- email: contacto@datzin.com.mx

Include the affected version, a description, and steps or a proof of concept. You will get an acknowledgement
within 5 business days, and a fix or mitigation plan once the report is confirmed.

## Scope notes

- Traces (`.vizc` files) contain values from the traced program. VizControl redacts common secret names and
  values before writing them, but treat trace files as sensitive. They are created readable by their owner only.
- `vizcontrol serve` is a local, single-user server. It binds to `127.0.0.1` and rejects requests addressed to
  other host names (DNS rebinding). Binding it to another interface (`--host`) exposes your traces to that network.
- Configuration hooks (`[tool.vizcontrol]` `serializers`, `redact.hook`) import and run code from your project, like
  any other tool configured in `pyproject.toml`. Only use configuration you trust.
