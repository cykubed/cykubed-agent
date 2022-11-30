import cfg from './cypress.config.js';

const specconfig = {
  e2e_include: cfg.default?.e2e?.specPattern || 'cypress/e2e/**/*.cy.{js,jsx,ts,tsx}',
  e2e_exclude: cfg.default?.e2e?.excludeSpecPattern || '*.hot-update.js',
  component_include: cfg.default?.component?.specPattern || '**/*.cy.{js,jsx,ts,tsx}',
  component_exclude: cfg.default?.component?.excludeSpecPattern || ''
}
console.log(JSON.stringify(specconfig));

