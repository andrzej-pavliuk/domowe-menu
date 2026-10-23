/* Runtime render smoke tests without external packages or a browser. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(){return {innerHTML:'',textContent:'',dataset:{},open:false,addEventListener(){},showModal(){this.open=true},close(){this.open=false},scrollTop:0}}
const context=vm.createContext({console,Intl,URL,Date,JSON,Number,String,Math,Map,Set,Array,Object,Promise,Error,FormData:class{},document:{body:element(),querySelector(s){if(!elements.has(s))elements.set(s,element());return elements.get(s)},querySelectorAll(){return []},addEventListener(){}},localStorage:{getItem(){return null},setItem(){}},navigator:{},matchMedia(){return {matches:false}},fetch:async()=>({ok:true,status:200,json:async()=>({authenticated:false})}),setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},window:{scrollTo(){},print(){}},location:{reload(){}}});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8'),context);
vm.runInContext(`S={version:'0.4.0',profiles:[{id:1,name:'Ja',portion_factor:1,participates:1,calories:2500,tolerance_type:'percent',tolerance_value:5,calculation:{}}],recipes:[{id:1,name:'Ryż <script>bad</script>',ingredients:[{name:'cukier',qty:1,unit:'łyżeczka',grams_each:5}],meal_types:['Obiad'],seasons:[],servings:2,calories:500,active_minutes:10,total_minutes:30,instructions:'Ugotuj ryż.\\nDodaj cukier.',category:'Obiad',tags:'szybkie'}],meals:[{id:'meal',batch_id:'batch',recipe_id:1,profile_id:null,meal:'Obiad',date:day,prepare_date:day,portions:{'1':1},recipe:null}],foods:[{id:1,name:'cukier',calories:400,measure:'łyżeczka',grams_each:5,form:'fresh',months:'[]'}],rules:[],weights:[{id:1,profile_id:1,date:day,weight:80}],diary:[],lists:[],defaults:{repeat_days:{Śniadanie:1,Obiad:2,Kolacja:1},active_limits:{Śniadanie:15,Obiad:35,Kolacja:15},season_mode:'strict'}};S.meals[0].recipe=S.recipes[0];pid=1;recipeId=1;`,context);
for(const page of ['home','recipes','detail','planner','diary','household','goals','progress','products','rules','settings','prep','collections','cook','shopping']){
 vm.runInContext(`page='${page}';render()`,context);assert.ok(elements.get('#main').innerHTML.length>50,page);
 assert.ok(!elements.get('#main').innerHTML.includes('<script>bad</script>'),`Escaping in ${page}`);
}
for(const call of ['recipeForm(1)','profileForm(1)','foodForm(1)','planForm(1)','ruleForm()','generatorForm()','diaryForm(1)']){
 vm.runInContext(call,context);assert.ok(elements.get('#dialog-body').innerHTML.includes('<form'),call);
}
assert.ok(elements.get('#dialog-body').innerHTML.includes('name="grams"'),'Product diary includes grams');
assert.ok(elements.get('#dialog-body').innerHTML.includes('name="meal"'),'Product diary includes meal');
console.log('OK: 15 views, 7 forms, text escaping and product diary fields');
// Desktop uses the same planning actions: verify that a clicked slot keeps its
// date and meal type, and the calendar presents all days rather than one day.
vm.runInContext("page='planner';month=false;render()",context);
assert.equal((elements.get('#main').innerHTML.match(/class="dm-board-day /g)||[]).length,7,'Whole week is rendered');
assert.ok(elements.get('#sidebar').innerHTML.includes('aria-current="page"'),'Current section is marked in the sidebar');
assert.ok(elements.get('#sidebar').innerHTML.includes('Produkty i miary'),'Secondary sections are reachable directly');
vm.runInContext("month=true;render()",context);
assert.equal((elements.get('#main').innerHTML.match(/class="dm-board-day /g)||[]).length,42,'Month includes adjacent weeks');
vm.runInContext("action('plan-slot','2026-11-03|Kolacja')",context);
const slot=elements.get('#dialog-body').innerHTML;
assert.ok(slot.includes('name="date" type="date" value="2026-11-03"'),'Slot keeps selected date');
assert.ok(slot.includes('value="Kolacja" selected'),'Slot keeps selected meal type');
vm.runInContext("login()",context);
assert.equal(elements.get('#sidebar').innerHTML,'','Navigation is cleared on logout');
assert.equal(context.document.body.dataset.auth,'false');
console.log('OK: desktop week/month, navigation, slot planning and login state');
