<h1>requirements-from-imports</h1>

<p>
  Generate a <code>requirements.txt</code> pinned to the exact versions installed in your environment, based on the imports actually used in your codebase.
</p>

<h2>Features</h2>
<ul>
  <li>Recursively scans all <code>*.py</code> files from a project root</li>
  <li>Follows <strong>local imports</strong> (your own packages/modules) so they’re scanned but <strong>not</strong> added to requirements</li>
  <li>Excludes Python <strong>stdlib</strong></li>
  <li>Maps top-level imports to installed <strong>distributions</strong> and pins <code>name==version</code></li>
  <li>Zero external dependencies; works in virtualenvs out of the box</li>
</ul>

<h2>Why?</h2>
<p>
  <code>pip freeze</code> lists everything installed—even packages you don’t use. This tool writes requirements for only what your code imports, with the versions from the environment you ran it in.
</p>

<h2>Quickstart</h2>
<ol>
  <li>Put the script at your project root as <code>gen_requirements.py</code>.</li>
  <li>Activate the environment that has your project’s packages installed.</li>
  <li>Run:</li>
</ol>

<pre><code>python gen_requirements.py --root . --out requirements.txt
</code></pre>

<p>
  That’s it. The generated <code>requirements.txt</code> will contain only third-party packages your code actually imports, pinned to the versions currently installed.
</p>

<h2>CLI</h2>
<pre><code>--root PATH        Project root to scan (default: .)
--out  FILE        Output file (default: requirements.txt)
</code></pre>

<h2>How it works (high level)</h2>
<ol>
  <li><strong>Parse imports</strong> with <code>ast</code> from every <code>*.py</code> under <code>--root</code>.</li>
  <li><strong>Classify</strong> each import as <em>local</em> (your files), <em>stdlib</em>, or <em>thirdparty</em> using:
    <ul>
      <li>Presence of local files (<code>pkg/__init__.py</code>, <code>name.py</code>)</li>
      <li><code>importlib.util.find_spec</code> and path checks (stdlib vs site-packages)</li>
    </ul>
  </li>
  <li><strong>Resolve</strong> distributions &amp; versions via <code>importlib.metadata</code> (PEP 503 name normalization).</li>
  <li><strong>Write</strong> <code>requirements.txt</code> with <code>dist==version</code>.</li>
</ol>

<h2>Exclusions &amp; defaults</h2>
<ul>
  <li>Skips common junk folders: <code>.git</code>, <code>__pycache__</code>, <code>.venv</code>, <code>venv</code>, <code>env</code>, <code>build</code>, <code>dist</code>, etc.</li>
  <li>Skips files ending with <code>_pb2.py</code> (customizable).</li>
  <li>Relative imports (<code>from . import x</code>) are treated as <strong>local</strong>.</li>
</ul>

<p>You can tweak these in the constants near the top of the script:</p>
<pre><code>EXCLUDE_DIRS = {...}
EXCLUDE_FILE_SUFFIXES = {"_pb2.py"}
</code></pre>

<h2>Edge cases &amp; tips</h2>
<ul>
  <li>Dynamic imports (e.g., <code>__import__(name)</code>) aren’t detected; add those packages manually.</li>
  <li>Some packages provide multiple top-level modules; the script heuristically picks a matching distribution.</li>
  <li>Run inside your active <strong>venv</strong> to pin the versions you actually deploy.</li>
  <li>If something can’t be mapped, the script prints a warning with the module name.</li>
</ul>

<h2>Examples</h2>
<p><strong>Pin requirements for the current folder:</strong></p>
<pre><code>python gen_requirements.py
</code></pre>

<p><strong>Scan a subfolder and write to a custom file:</strong></p>
<pre><code>python gen_requirements.py --root src --out reqs.dev.txt
</code></pre>

<h2>CI automation (optional)</h2>
<p>Make sure <code>requirements.txt</code> stays in sync on every push:</p>

<pre><code class="language-yaml"># .github/workflows/reqs.yml
name: Generate requirements
on: [push, pull_request]
jobs:
  reqs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install your project deps
        run: |
          python -m pip install --upgrade pip
          # install your project deps here (editable/poetry/pip, etc.)
      - name: Generate requirements.txt
        run: python gen_requirements.py --root . --out requirements.txt
      - name: Show diff
        run: git diff -- requirements.txt || true
</code></pre>

<h2>Contributing</h2>
<p>
  PRs and issues are welcome! Please keep the script dependency-free and tested on Python ≥ 3.8.
</p>
