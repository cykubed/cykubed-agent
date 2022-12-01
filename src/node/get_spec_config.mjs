import(process.argv[2]+"/cypress.config.js").then(cfg => {
  const config = cfg.default.default;
  const specconfig = {
    e2e_include: config?.e2e?.specPattern || 'cypress/e2e/**/*.cy.{js,jsx,ts,tsx}',
    e2e_exclude: config?.e2e?.excludeSpecPattern || '*.hot-update.js',
    component_include: config?.component?.specPattern || '**/*.cy.{js,jsx,ts,tsx}',
    component_exclude: config?.component?.excludeSpecPattern
  }
  console.log(JSON.stringify(specconfig));
});


