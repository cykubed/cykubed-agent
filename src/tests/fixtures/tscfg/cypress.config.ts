import {defineConfig} from "cypress";

export default defineConfig({
  video: false,
  chromeWebSecurity: false,

  e2e: {
    baseUrl: 'http://localhost:4200',
    specPattern: 'cypress/xe2e/**/*.cy.{js,ts}',
    excludeSpecPattern: 'cypress/xe2e/tests/test3.cy.*'
  }
});
