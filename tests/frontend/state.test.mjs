import test from 'node:test';
import assert from 'node:assert/strict';
import {parseRoute,routeHash,Revision} from '../../src/mockazure/console/static/state.js';
test('routes preserve scoped record paths without request bodies',()=>{
 const value={view:'resources',tenant:'abc',family:'storage.files',record:'a/b c',parent:'share/folder',payload:'secret'};
 assert.deepEqual(parseRoute(routeHash(value)),{view:'resources',tenant:'abc',family:'storage.files',record:'a/b c',parent:'share/folder'});
 assert.equal(parseRoute('#/unknown').view,'resources');
});
test('changing away and back invalidates an earlier response',()=>{
 const revision=new Revision(); const token=revision.capture();
 assert.equal(revision.current(token),true);revision.invalidate();revision.invalidate();
 assert.equal(revision.current(token),false);
});
