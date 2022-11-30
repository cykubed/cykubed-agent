context('this is test2', () => {

  beforeEach( ()=> {
    cy.visit('/');
  });

  it.skip('skipped test', () => {
    cy.get('h1').should('contain.text', 'Dummy UI');
  });

  it('non-skipped test that passes', () => {
    cy.get('h1').should('contain.text', 'Dummy UI');
  })
});
