import pkg from 'glob';
import {readFileSync} from 'node:fs';

const {glob} = pkg;
const wdir = process.argv[2];
process.chdir(wdir);

import("./cypress.config.js").then(cfg => {
  const config = cfg.default.default;
  const specconfig = {
    e2e_include: config?.e2e?.specPattern || 'cypress/e2e/**/*.cy.{js,jsx,ts,tsx}',
    e2e_exclude: config?.e2e?.excludeSpecPattern || '*.hot-update.js',
    component_include: config?.component?.specPattern || '**/*.cy.{js,jsx,ts,tsx}',
    component_exclude: config?.component?.excludeSpecPattern
  }

  const specs = new Set(glob.sync(specconfig.e2e_include, {}));
  specs.add(...glob.sync(specconfig.component_include, {}));

  for (const k of glob.sync(specconfig.e2e_exclude, {})) {
    specs.delete(k);
  }
  if (specconfig.component_exclude) {
    for (const k of glob.sync(specconfig.component_exclude, {})) {
      specs.delete(k);
    }
  }
  console.log(JSON.stringify(Array.from(specs)));

}, () => {
  // no modern config file - fallback on JSON
  const data = JSON.parse(readFileSync('cypress.json', {encoding: 'utf8'}));
  const folder = data['integrationFolder'] || 'cypress/integration';
  const globspec = data['testFiles'] || '**/*.*';
  const specs = glob.sync(globspec, {cwd: folder}).map(f => folder+'/'+f);
  console.log(JSON.stringify(specs));
});


