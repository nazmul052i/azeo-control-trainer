"""Console furniture shared by every operator station.

The chrome (`docs/console/07` budget), Azeo's menu+navigation bar,
the publish-then-PULL `DeploymentRules`, and the back/forward
stack. None of it knows a document format: `DeploymentRules`
binds to a store by four questions and `NavigationStack` holds
ids, which is what let one set of rules serve two stacks.

Promoted out of the DynaLive package when that stack was
archived.
"""
