"""All Cypher used by the Cypher Circle application."""

SMOKE_RETURN_ONE = "RETURN 1 AS value"

COUNT_ALL_NODES = """
MATCH (n)
RETURN count(n) AS nodes
""".strip()

RESET_DATABASE = """
CALL () {
  MATCH (n)
  DETACH DELETE n
} IN TRANSACTIONS OF 5000 ROWS
""".strip()

CREATE_USER_USERNAME_CONSTRAINT = """
CREATE CONSTRAINT user_username_unique IF NOT EXISTS
FOR (u:User) REQUIRE u.username IS UNIQUE
""".strip()

CREATE_USER_EMAIL_CONSTRAINT = """
CREATE CONSTRAINT user_email_unique IF NOT EXISTS
FOR (u:User) REQUIRE u.email IS UNIQUE
""".strip()

CREATE_USER_SOURCE_ID_CONSTRAINT = """
CREATE CONSTRAINT user_source_id_unique IF NOT EXISTS
FOR (u:User) REQUIRE u.source_id IS UNIQUE
""".strip()

CREATE_CIRCLE_ID_CONSTRAINT = """
CREATE CONSTRAINT circle_id_unique IF NOT EXISTS
FOR (c:Circle) REQUIRE c.circle_id IS UNIQUE
""".strip()

CREATE_USER_NAME_TEXT_INDEX = """
CREATE TEXT INDEX user_name_lc_text IF NOT EXISTS
FOR (u:User) ON (u.name_lc)
""".strip()

CREATE_USER_USERNAME_TEXT_INDEX = """
CREATE TEXT INDEX user_username_text IF NOT EXISTS
FOR (u:User) ON (u.username)
""".strip()

LOAD_CIRCLES = """
UNWIND $rows AS row
MERGE (c:Circle {circle_id: row.circle_id})
SET c.name = row.name,
    c.source_label = row.source_label,
    c.owner_source_id = row.owner_source_id,
    c.source = 'snap',
    c.load_token = $load_token
RETURN count(*) AS loaded
""".strip()

LOAD_USERS = """
UNWIND $rows AS row
MERGE (u:User {source_id: row.source_id})
SET u.username = row.username,
    u.email = row.email,
    u.name = row.name,
    u.name_lc = row.name_lc,
    u.password_hash = row.password_hash,
    u.bio = row.bio,
    u.location = row.location,
    u.joined_at = datetime(row.joined_at),
    u.source = 'snap',
    u.load_token = $load_token
RETURN count(*) AS loaded
""".strip()

LOAD_MEMBERSHIPS = """
UNWIND $rows AS row
MATCH (u:User {source_id: row.source_id})
MATCH (c:Circle {circle_id: row.circle_id, source: 'snap'})
MERGE (u)-[r:IN_CIRCLE]->(c)
SET r.load_token = $load_token
RETURN count(*) AS loaded
""".strip()

LOAD_FOLLOWS = """
UNWIND $rows AS row
MATCH (a:User {source_id: row.src})
MATCH (b:User {source_id: row.dst})
MERGE (a)-[r:FOLLOWS]->(b)
ON CREATE SET r.since = datetime(row.since)
SET r.load_token = $load_token
RETURN count(*) AS loaded
""".strip()

VERIFY_COUNTS = """
RETURN count { (:User) } AS users,
       count { (:Circle) } AS circles,
       count { ()-[:FOLLOWS]->() } AS follows,
       count { ()-[:IN_CIRCLE]->() } AS memberships
""".strip()

VERIFY_DATASET_COUNTS = """
MATCH (u:User {source: 'snap'})
WITH count(u) AS users
CALL () {
  MATCH (c:Circle)
  WHERE c.source = 'snap'
  RETURN count(c) AS circles
}
CALL () {
  MATCH (a:User {source: 'snap'})-[:FOLLOWS]->(b:User {source: 'snap'})
  RETURN count(*) AS follows
}
CALL () {
  MATCH (a:User {source: 'snap'})-[:IN_CIRCLE]->(c:Circle {source: 'snap'})
  RETURN count(*) AS memberships
}
RETURN users, circles, follows, memberships
""".strip()

MARK_EXISTING_CIRCLES_AS_SNAP = """
MATCH (c:Circle)
WHERE c.source IS NULL
SET c.source = 'snap'
RETURN count(c) AS marked
""".strip()

AWAIT_INDEXES = """
CALL db.awaitIndexes()
""".strip()

CLEAR_LOAD_TOKEN = """
WITH $load_token AS load_token
CALL (load_token) {
  MATCH ()-[r]-()
  WHERE r.load_token = load_token
  DELETE r
  RETURN count(*) AS deleted_relationships
}
CALL (load_token) {
  MATCH (n)
  WHERE n.load_token = load_token AND NOT (n)--()
  DELETE n
  RETURN count(*) AS deleted_nodes
}
RETURN deleted_relationships, deleted_nodes
""".strip()

FINALIZE_LOAD = """
WITH $load_token AS load_token
CALL (load_token) {
  MATCH (n)
  WHERE n.load_token = load_token
  REMOVE n.load_token
  RETURN count(n) AS nodes
}
CALL (load_token) {
  MATCH ()-[r]-()
  WHERE r.load_token = load_token
  REMOVE r.load_token
  RETURN count(r) AS relationships
}
RETURN nodes, relationships
""".strip()

CREATE_USER = """
CREATE (u:User {
  username: $username,
  email: $email,
  name: $name,
  name_lc: toLower($name),
  password_hash: $password_hash,
  bio: $bio,
  location: $location,
  joined_at: datetime($joined_at),
  source: 'app'
})
RETURN u.username AS username, u.name AS name
""".strip()

CREATE_USER_WITH_CIRCLES = """
CREATE (u:User {
  username: $username,
  email: $email,
  name: $name,
  name_lc: toLower($name),
  password_hash: $password_hash,
  bio: $bio,
  location: $location,
  joined_at: datetime(),
  source: 'app'
})
WITH u, $circle_ids AS circle_ids
CALL (u, circle_ids) {
  MATCH (c:Circle)
  WHERE c.circle_id IN circle_ids
  WITH c ORDER BY c.circle_id ASC
  RETURN collect(c) AS circles
}
FOREACH (circle IN circles | MERGE (u)-[:IN_CIRCLE]->(circle))
RETURN u.username AS username, u.name AS name
""".strip()

CHECK_REGISTRATION_CONFLICTS = """
OPTIONAL MATCH (a:User {username: $username})
OPTIONAL MATCH (b:User {email: $email})
RETURN a IS NOT NULL AS username_taken,
       b IS NOT NULL AS email_taken
""".strip()

GET_TOP_CIRCLES = """
MATCH (c:Circle)
RETURN c.circle_id AS circle_id, c.name AS name,
       count { (:User)-[:IN_CIRCLE]->(c) } AS members
ORDER BY members DESC, name ASC, circle_id ASC
LIMIT $limit
""".strip()

GET_PROFILE = """
MATCH (u:User {username: $username})
CALL (u) {
  MATCH (u)-[:IN_CIRCLE]->(c:Circle)
  WITH c ORDER BY c.name ASC, c.circle_id ASC
  RETURN collect(c.name) AS circle_names
}
RETURN u.username AS username, u.name AS name, u.email AS email,
       u.bio AS bio, u.location AS location, u.joined_at AS joined_at,
       circle_names[..5] AS circles, size(circle_names) AS circle_count,
       count { (u)-[:FOLLOWS]->(:User) } AS following_count,
       count { (:User)-[:FOLLOWS]->(u) } AS followers_count
""".strip()

UPDATE_PROFILE = """
MATCH (u:User {username: $username})
SET u.name = coalesce($name, u.name),
    u.name_lc = toLower(coalesce($name, u.name)),
    u.bio = coalesce($bio, u.bio),
    u.location = coalesce($location, u.location),
    u.updated_at = datetime()
RETURN u.username AS username
""".strip()

CLEAR_BIO = """
MATCH (u:User {username: $username})
SET u.bio = null, u.updated_at = datetime()
RETURN u.username AS username
""".strip()

CLEAR_LOCATION = """
MATCH (u:User {username: $username})
SET u.location = null, u.updated_at = datetime()
RETURN u.username AS username
""".strip()

JOIN_CIRCLE = """
MATCH (u:User {username: $username}), (c:Circle {circle_id: $circle_id})
MERGE (u)-[:IN_CIRCLE]->(c)
""".strip()

LEAVE_CIRCLE = """
MATCH (u:User {username: $username})-[r:IN_CIRCLE]->(c:Circle {circle_id: $circle_id})
DELETE r
""".strip()

GET_MY_CIRCLES = """
MATCH (u:User {username: $username})-[:IN_CIRCLE]->(c:Circle)
RETURN c.circle_id AS circle_id, c.name AS name
ORDER BY name ASC, circle_id ASC
""".strip()

CIRCLE_EXISTS = """
MATCH (c:Circle {circle_id: $circle_id})
RETURN count(c) > 0 AS exists
""".strip()

UNFOLLOW_USER = """
MATCH (me:User {username: $me})-[r:FOLLOWS]->(target:User {username: $target})
DELETE r
""".strip()

USER_EXISTS = """
MATCH (u:User {username: $username})
RETURN count(u) > 0 AS exists
""".strip()

COUNT_CONNECTIONS = """
MATCH (me:User {username: $username})
RETURN count { (me)-[:FOLLOWS]->(:User) } AS following,
       count { (:User)-[:FOLLOWS]->(me) } AS followers
""".strip()

GET_FOLLOWING = """
MATCH (me:User {username: $username})-[r:FOLLOWS]->(u:User)
RETURN u.username AS username, u.name AS name, u.location AS location,
       r.since AS since,
       EXISTS { (u)-[:FOLLOWS]->(me) } AS follows_back
ORDER BY since DESC, username ASC
SKIP $skip LIMIT $limit
""".strip()

GET_FOLLOWERS = """
MATCH (u:User)-[r:FOLLOWS]->(me:User {username: $username})
RETURN u.username AS username, u.name AS name, u.location AS location,
       r.since AS since,
       EXISTS { (me)-[:FOLLOWS]->(u) } AS i_follow_back
ORDER BY since DESC, username ASC
SKIP $skip LIMIT $limit
""".strip()

GET_EGO_NETWORK = """
MATCH (me:User {username: $username})
CALL (me) {
  MATCH (me)-[:FOLLOWS]->(o:User)
  WITH o ORDER BY o.username ASC
  RETURN collect(o)[..$cap] AS outs
}
CALL (me) {
  MATCH (i:User)-[:FOLLOWS]->(me)
  WITH i ORDER BY i.username ASC
  RETURN collect(i)[..$cap] AS ins
}
RETURN me.username AS center, me.name AS center_name,
       [x IN outs | {username: x.username, name: x.name}] AS following,
       [x IN ins  | {username: x.username, name: x.name}] AS followers
""".strip()

GET_EDGES_AMONG = """
MATCH (a:User)-[:FOLLOWS]->(b:User)
WHERE a.username IN $usernames AND b.username IN $usernames
RETURN a.username AS src, b.username AS dst
ORDER BY src ASC, dst ASC
LIMIT $edge_cap
""".strip()

COUNT_MUTUAL_FOLLOWING = """
MATCH (me:User {username: $me})-[:FOLLOWS]->(m:User)<-[:FOLLOWS]-(other:User {username: $other})
RETURN count(DISTINCT m) AS total
""".strip()

GET_MUTUAL_FOLLOWING = """
MATCH (me:User {username: $me})-[:FOLLOWS]->(m:User)<-[:FOLLOWS]-(other:User {username: $other})
RETURN m.username AS username, m.name AS name, m.location AS location
ORDER BY m.name ASC, m.username ASC
SKIP $skip LIMIT $limit
""".strip()

GET_RELATIONSHIP_STATUS = """
MATCH (me:User {username: $me}), (other:User {username: $other})
RETURN EXISTS { (me)-[:FOLLOWS]->(other) } AS i_follow,
       EXISTS { (other)-[:FOLLOWS]->(me) } AS they_follow
""".strip()

RECOMMEND_BY_CIRCLE = """
MATCH (me:User {username: $username})-[:IN_CIRCLE]->(c:Circle)<-[:IN_CIRCLE]-(rec:User)
WHERE rec <> me AND NOT (me)-[:FOLLOWS]->(rec) AND NOT rec.username IN $exclude
WITH rec, min(c.name) AS circle
WITH rec, circle, count { (:User)-[:FOLLOWS]->(rec) } AS followers
RETURN rec.username AS username, rec.name AS name, rec.location AS location,
       followers, circle
ORDER BY followers DESC, username ASC
LIMIT $limit
""".strip()

GET_FOLLOWING_USERNAMES = """
MATCH (:User {username: $username})-[:FOLLOWS]->(u:User)
WITH u ORDER BY u.username ASC
RETURN collect(u.username) AS usernames
""".strip()

GET_POPULAR_USERS = """
MATCH (u:User)
WHERE NOT u.username IN $exclude
WITH u, count { (:User)-[:FOLLOWS]->(u) } AS followers
RETURN u.username AS username, u.name AS name, u.location AS location, followers
ORDER BY followers DESC, username ASC
LIMIT $limit
""".strip()

SEARCH_USERS = """
MATCH (u:User)
WHERE u.username <> $me
  AND (u.name_lc CONTAINS $q OR u.username CONTAINS $q)
WITH u,
     count { (:User)-[:FOLLOWS]->(u) } AS followers,
     CASE WHEN u.username STARTS WITH $q OR u.name_lc STARTS WITH $q THEN 0 ELSE 1 END AS rank
RETURN u.username AS username, u.name AS name, u.location AS location, followers,
       EXISTS { (:User {username: $me})-[:FOLLOWS]->(u) } AS i_follow
ORDER BY rank, followers DESC, username ASC
LIMIT $limit
""".strip()

DELETE_USER_BY_USERNAME = """
MATCH (u:User {username: $username})
DETACH DELETE u
""".strip()

CREATE_TEST_CIRCLE = """
CREATE (c:Circle {
  circle_id: $circle_id,
  name: $name,
  source: 'integration_test'
})
RETURN c.circle_id AS circle_id, c.name AS name
""".strip()

DELETE_TEST_CIRCLE = """
MATCH (c:Circle {circle_id: $circle_id, source: 'integration_test'})
DETACH DELETE c
""".strip()

CREATE_SLICE_USER = """
CREATE (u:User {
  username: $username,
  email: $email,
  name: $name,
  name_lc: toLower($name),
  password_hash: $password_hash,
  bio: $bio,
  location: $location,
  joined_at: datetime($joined_at),
  source: 'slice_check'
})
RETURN u.username AS username, u.name AS name
""".strip()

GET_AUTH_BY_USERNAME = """
MATCH (u:User {username: $username})
RETURN u.password_hash AS password_hash, u.name AS name
""".strip()

FOLLOW_USER = """
MATCH (me:User {username: $me}), (target:User {username: $target})
WHERE me <> target
MERGE (me)-[r:FOLLOWS]->(target)
ON CREATE SET r.since = datetime()
RETURN r.since AS since
""".strip()

RECOMMEND_BY_NETWORK = """
MATCH (me:User {username: $username})-[:FOLLOWS]->(mid:User)-[:FOLLOWS]->(rec:User)
WHERE rec <> me AND NOT (me)-[:FOLLOWS]->(rec)
WITH rec, mid ORDER BY mid.username ASC
WITH rec, count(DISTINCT mid) AS score, collect(DISTINCT mid.username)[..3] AS via
RETURN rec.username AS username,
       rec.name AS name,
       rec.location AS location,
       count { (:User)-[:FOLLOWS]->(rec) } AS followers,
       score,
       via
ORDER BY score DESC, followers DESC, username ASC
LIMIT $limit
""".strip()

DELETE_SLICE_USER = """
MATCH (u:User {username: $username, source: 'slice_check'})
DETACH DELETE u
""".strip()


