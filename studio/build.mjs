import { build } from "esbuild";
import { readFile, writeFile, readdir } from "node:fs/promises";
import path from "node:path";

const outdir = "../turnloom/studio_assets";
const result = await build({
  entryPoints: ["App.jsx"],
  outfile: outdir + "/studio.js",
  bundle: true,
  minify: true,
  metafile: true,
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "inline",
  logLevel: "info",
});
const packages = new Set();
for (const name of Object.keys(result.metafile.inputs)) {
  const parts = name.split("node_modules/").pop().split("/");
  if (name.includes("node_modules/"))
    packages.add(
      parts[0].startsWith("@") ? parts.slice(0, 2).join("/") : parts[0],
    );
}
let notices = "";
for (const name of [...packages].sort()) {
  const folder = path.join("node_modules", name);
  const manifest = JSON.parse(
    await readFile(path.join(folder, "package.json"), "utf8"),
  );
  const legal = (await readdir(folder)).filter((file) =>
    /^(licen[sc]e|notice)(\.|$)/i.test(file),
  );
  if (!legal.length && name !== "@cline/ui" && !manifest.license)
    throw new Error("Missing license for " + name);
  notices +=
    "\n" + name + " " + manifest.version + "  " + manifest.license + "\n";
  for (const file of legal)
    notices += (await readFile(path.join(folder, file), "utf8")) + "\n";
  if (!legal.length && name !== "@cline/ui")
    notices +=
      "The package ships no license file. Its manifest declares " +
      manifest.license +
      ". Source: " +
      (manifest.repository?.url || manifest.homepage || "https://www.npmjs.com/package/" + name) +
      "\n";
  if (name === "@cline/ui")
    notices += (await readFile("CLINE-LICENSE", "utf8")) + "\n";
}
await writeFile(outdir + "/THIRD-PARTY.txt", notices.trim() + "\n");
