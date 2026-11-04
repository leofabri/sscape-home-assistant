// Run with node --test tests/setup-controller.test.mjs. No HA server or network required.
import {readFile} from "node:fs/promises";
import test from "node:test";
import assert from "node:assert/strict";
const source=await readFile(new URL("../custom_components/sscape/www/setup-controller.js",import.meta.url),"utf8");
const {createDashboard, action}=await import("data:text/javascript;base64,"+Buffer.from(source).toString("base64"));
const config={title:"Test",views:[{path:"sscape",cards:[]}]};
function client(fail="") {
  const calls=[];
  return {user:{is_admin:true},calls,async callWS(value){
    calls.push(value);
    if(value.type===fail)throw Error("fictional failure");
    if(value.type==="lovelace/dashboards/create")return {id:value.url_path.replaceAll("-","_"),url_path:value.url_path};
    return {response:{targets:[]}};
  }};
}
test("no writes before explicit confirmation or by non-admin",async()=>{
  const hass=client();await assert.rejects(createDashboard(hass,config,"Test",false));assert.equal(hass.calls.length,0);
  hass.user.is_admin=false;await assert.rejects(createDashboard(hass,config,"Test",true));assert.equal(hass.calls.length,0);
});
test("creates and saves only a new URL; enable list is explicit",async()=>{
  const hass=client();const result=await createDashboard(hass,config,"Test",true,["sensor.a","sensor.a"]);
  assert.match(result.url,/^\/sscape-[a-f0-9]+\/sscape$/);
  assert.equal(hass.calls[0].url_path,hass.calls[1].url_path);
  assert.deepEqual(hass.calls.map(v=>v.type),["lovelace/dashboards/create","lovelace/config/save","config/entity_registry/update"]);
});
test("collision never saves, enables or deletes an existing dashboard",async()=>{
  const hass=client("lovelace/dashboards/create");await assert.rejects(createDashboard(hass,config,"Test",true));
  assert.equal(hass.calls.length,1);
});
test("failed save rolls back the exact new id, not existing dashboards",async()=>{
  const hass=client("lovelace/config/save");await assert.rejects(createDashboard(hass,config,"Test",true));
  assert.equal(hass.calls[2].dashboard_id,hass.calls[0].url_path.replaceAll("-","_"));
  assert.equal(hass.calls[2].type,"lovelace/dashboards/delete");
});
test("malformed creation response never saves or deletes",async()=>{
  const hass=client();hass.callWS=async v=>{hass.calls.push(v);return {id:"existing",url_path:"existing"}};
  await assert.rejects(createDashboard(hass,config,"Test",true));assert.equal(hass.calls.length,1);
});
test("failed optional enable leaves created dashboard usable",async()=>{
  const hass=client("config/entity_registry/update");const result=await createDashboard(hass,config,"Test",true,["sensor.a"]);
  assert.deepEqual(result.failed,["sensor.a"]);assert.ok(result.url);
});
test("preview uses response-only service",async()=>{
  const hass=client();assert.deepEqual(await action(hass,"get_setup"),{targets:[]});
  assert.equal(hass.calls[0].return_response,true);
});
