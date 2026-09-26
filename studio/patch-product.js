// Turns VSCodium's product.json into the one of MyDevAgent Studio, English edition (used by studio/build.ps1).
//   node patch-product.js <path to product.json>
const fs = require("fs");

const file = process.argv[2];
const product = JSON.parse(fs.readFileSync(file, "utf8"));
Object.assign(product, {
  nameShort: "MyDevAgent Studio",
  nameLong: "MyDevAgent Studio",
  // everything below is separate from VSCodium and from the Italian edition, so they can all be installed together
  dataFolderName: ".mydevagent-studio-en", // extensions and settings
  serverDataFolderName: ".mydevagent-studio-en-server",
  urlProtocol: "mydevagent-studio-en",
  win32MutexName: "mydevagentstudioen",
  win32AppUserModelId: "MyDevAgent.Studio.EN", // its own taskbar icon (must match AppUserModelID in installer.iss)
  win32DirName: "MyDevAgent Studio EN",
  win32NameVersion: "MyDevAgent Studio",
  win32RegValueName: "MyDevAgentStudioEN",
  win32ShellNameShort: "MyDevAgent Studio",
});
delete product.updateUrl; // no VSCodium updates replacing Studio
delete product.checksums; // we change icons and watermark: with checksums, VSCodium would say "installation corrupt"
fs.writeFileSync(file, JSON.stringify(product, null, "\t") + "\n");
console.log(`product.json: ${product.nameLong} (VSCodium ${product.version})`);
