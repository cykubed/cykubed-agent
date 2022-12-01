import {defineConfig} from "cypress";

export default defineConfig({
  video: false,
  chromeWebSecurity: false,

  e2e: {
    baseUrl: 'http://localhost:4200',
    specPattern: 'cypress/e2e/**/*.cy.{js,jsx,ts,tsx}',
    excludeSpecPattern: 'cypress/e2e/tests/test3.cy.*'
  }
});
