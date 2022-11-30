context('test1', () => {

  beforeEach( ()=> {
    cy.visit('/');
  });

  it('should have the correct title', () => {
    cy.get('h1').should('contain.text', 'Dummy UI');
    cy.screenshot();
  })

  it('this will fail', () => {
    cy.get('h2').should('contain.text', 'xxx');
  });

  it.skip('this will be skipped', () => {
    cy.get('h2').should('contain.text', 'xxx');
  });
});
