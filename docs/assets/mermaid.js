// Normalize plain Markdown fences and Jekyll/Rouge wrappers without losing source text.
for (const block of document.querySelectorAll(".language-mermaid")) {
  if (!block.isConnected) continue;
  const source = (block.querySelector("code") || block).textContent;
  const node = document.createElement("pre");
  node.className = "mermaid";
  node.textContent = source;
  (block.closest("pre") || block).replaceWith(node);
}

const korean = document.documentElement.lang === "ko";
const diagrams = Array.from(document.querySelectorAll("pre.mermaid"), (node) => {
  if (!node.closest(".mermaid-scroll")) {
    const scroll = document.createElement("div");
    scroll.className = "mermaid-scroll";
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", korean ? "흐름도; 가로로 스크롤하여 그림을 확인합니다" : "Flowchart; scroll horizontally to inspect the diagram");
    scroll.tabIndex = 0;
    node.before(scroll);
    scroll.append(node);
  }
  return { node, source: node.textContent };
});

function restoreSource({ node, source }) {
  node.textContent = source;
  node.removeAttribute("data-processed");
}

// A blocked CDN or rendering error leaves readable inline text in place.
if (diagrams.length) {
  try {
    const { default: mermaid } = await import("https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.esm.min.mjs");
    const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
    let rendering = Promise.resolve();

    function renderDiagrams() {
      rendering = rendering.then(async () => {
        const styles = getComputedStyle(document.documentElement);
        const color = (name) => styles.getPropertyValue(name).trim();
        const choice = document.documentElement.dataset.theme;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: {
            darkMode: choice === "dark" || (choice === "system" && colorScheme.matches),
            fontFamily: getComputedStyle(document.body).fontFamily,
            fontSize: "15px",
            primaryColor: color("--surface-2"),
            primaryTextColor: color("--ink"),
            primaryBorderColor: color("--accent"),
            lineColor: color("--muted"),
            edgeLabelBackground: color("--surface"),
            secondaryColor: color("--surface"),
            tertiaryColor: color("--surface")
          },
          flowchart: { htmlLabels: false, useMaxWidth: false, curve: "linear" }
        });
        for (const diagram of diagrams) {
          restoreSource(diagram);
          try {
            await mermaid.run({ nodes: [diagram.node] });
          } catch {
            restoreSource(diagram);
          }
        }
      }).catch(() => diagrams.forEach(restoreSource));
      return rendering;
    }

    new MutationObserver(renderDiagrams).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    colorScheme.addEventListener("change", () => {
      if (document.documentElement.dataset.theme === "system") renderDiagrams();
    });
    renderDiagrams();
  } catch {
    // The accessible inline Mermaid source remains available.
  }
}
