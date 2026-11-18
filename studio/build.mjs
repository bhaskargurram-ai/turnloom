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
  charset: "utf8",
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "inline",
  logLevel: "info",
});

// Third-party notices come from the packages that actually ended up in the bundle.
const packages = new Set();
for (const name of Object.keys(result.metafile.inputs)) {
  if (!name.includes("node_modules/")) continue;
  const parts = name.split("node_modules/").pop().split("/");
  packages.add(parts[0].startsWith("@") ? parts.slice(0, 2).join("/") : parts[0]);
}

// Licence files arrive with either line ending. One ending keeps the output identical on every system.
const unix = (text) => text.replace(/\r\n?/g, "\n").trimEnd();

async function vendored(name) {
  try {
    return await readFile(path.join("licenses", name.replace("/", "__") + ".txt"), "utf8");
  } catch {
    return null;
  }
}

const notices = [];
for (const name of [...packages].sort()) {
  const folder = path.join("node_modules", name);
  const manifest = JSON.parse(await readFile(path.join(folder, "package.json"), "utf8"));
  const legal = (await readdir(folder)).filter((file) => /^(licen[sc]e|notice)(\.|$)/i.test(file)).sort();
  const texts = [];
  for (const file of legal) texts.push(unix(await readFile(path.join(folder, file), "utf8")));
  if (!texts.length) {
    const text = await vendored(name);
    if (text === null)
      throw new Error(
        `${name} ${manifest.version} is bundled but ships no licence file. ` +
          `Add its licence text as studio/licenses/${name.replace("/", "__")}.txt.`,
      );
    texts.push(unix(text));
  }
  notices.push(`${name} ${manifest.version}  ${manifest.license}\n${texts.join("\n\n")}`);
}
await writeFile(outdir + "/THIRD-PARTY.txt", notices.join("\n\n") + "\n");
const size = result.metafile.outputs[Object.keys(result.metafile.outputs).find((file) => file.endsWith("studio.js"))].bytes;
console.log(`${packages.size} bundled packages, studio.js ${(size / 1024).toFixed(0)} KiB`);
