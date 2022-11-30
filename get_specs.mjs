import cfg from './cypress.config.js';
import pkg from 'glob';

const {glob} = pkg;

const specconfig = {
  e2e_include: cfg.default?.e2e?.specPattern || 'cypress/e2e/**/*.cy.{js,jsx,ts,tsx}',
  e2e_exclude: cfg.default?.e2e?.excludeSpecPattern || '*.hot-update.js',
  component_include: cfg.default?.component?.specPattern || '**/*.cy.{js,jsx,ts,tsx}',
  component_exclude: cfg.default?.component?.excludeSpecPattern || ''
}

const specs = glob.sync(specconfig.e2e_include, {});
console.log(JSON.stringify(specs));
// writeFileSync('config.json', JSON.stringify(specs));


