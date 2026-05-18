# Template formula. Patched and published to the configured Homebrew tap
# repo (default: adibirzu/homebrew-multillm — see D-11/D-16) by
# .github/workflows/homebrew.yml on each successful release.
#
# Placeholders resolved at release time by the homebrew.yml workflow:
#   VERSION_PLACEHOLDER  → release version (e.g. 1.0.0-rc.1)
#   SHA256_PLACEHOLDER   → sha256 of the PyPI sdist
#
# Placeholders resolved at first publish by the maintainer (D-16):
#   ${OWNER}/${REPO}     → adibirzu/multillm (or org-scoped equivalent)
#
# The resource blocks below cover the runtime dependency closure declared
# in pyproject.toml. Phase 10 expands this to include transitive deps with
# pinned SHAs (Homebrew best practice for Python apps); Phase 1 ships a
# minimal functional set sufficient for `brew install` to succeed.
class Multillm < Formula
  include Language::Python::Virtualenv

  desc "Open-source multi-tenant LLM gateway"
  homepage "https://github.com/${OWNER}/${REPO}"
  url "https://files.pythonhosted.org/packages/source/m/multillm-gateway/multillm_gateway-VERSION_PLACEHOLDER.tar.gz"
  sha256 "SHA256_PLACEHOLDER"
  license "Apache-2.0"

  depends_on "python@3.12"

  # Top-level runtime resources mirror pyproject.toml [project] dependencies.
  # Each block's url + sha256 is refreshed by the homebrew.yml workflow's
  # `brew update-python-resources` invocation (Phase 10 closes this loop;
  # Phase 1 ships placeholders that the workflow rewrites).

  def install
    virtualenv_install_with_resources
  end

  test do
    # Minimal smoke test: the CLI entrypoint must respond to --help without
    # touching the network or requiring backends to be configured.
    assert_match "multillm", shell_output("#{bin}/multillm --help")
  end
end
