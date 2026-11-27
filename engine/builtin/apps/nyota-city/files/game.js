/* kimi/game 0.2.0 — Apache-2.0 — https://github.com/kiplabat/kimi-game. Built on three.js (MIT). */
import*as ke from"three";import{game as oa,load as Ti,save as Ei}from"kimi/game";import{rng as Li}from"kimi/game";var te=72,N=8,X=N*te/2;var Je=11,we=te-2*Je,bs=3.5,U=c=>-X+c*te;var yt=c=>U(c)+te/2,D={towers:0,midrise:1,park:2,plaza:3,compound:4,shops:5,garage:6,gangTower:7,lowrise:8},Ze={safehouse:[3,3],plaza:[4,4],garage:[4,3],compound:[6,5],gangTower:[1,6],market:[2,4]};function Vs(c=2026){let e=Li(c),t=[];for(let i=0;i<N;i++){t.push([]);for(let o=0;o<N;o++){let a=Math.max(Math.abs(i-3.5),Math.abs(o-3.5)),n,r=e.next();a<1.6?n=r<.55?D.towers:r<.85?D.midrise:D.shops:a<2.6?n=r<.2?D.towers:r<.55?D.midrise:r<.7?D.park:r<.85?D.shops:D.compound:n=r<.3?D.lowrise:r<.5?D.park:r<.7?D.compound:r<.85?D.midrise:D.shops,t[i].push(n)}}let s=(i,o)=>t[Ze[i][0]][Ze[i][1]]=o;return s("safehouse",D.shops),s("plaza",D.plaza),s("garage",D.garage),s("compound",D.compound),s("gangTower",D.gangTower),s("market",D.shops),t[2][2]=D.park,t[5][5]=D.park,{use:t,seed:c}}function pe(c,e){let t=c*374761393+e*668265263>>>0;return t=Math.imul(t^t>>>13,1274126177)>>>0,((t^t>>>16)>>>0)/4294967296}import*as y from"three";import{BoxGeometry as Fi,Vector3 as tt}from"three";var et=new tt;function ae(c,e,t,s,i,o){let a=2*Math.PI*i/4,n=Math.max(o-2*i,0),r=Math.PI/4;et.copy(e),et[s]=0,et.normalize();let l=.5*a/(a+n),h=1-et.angleTo(c)/r;return Math.sign(et[t])===1?h*l:n/(a+n)+l+l*(1-h)}var fe=class c extends Fi{constructor(e=1,t=1,s=1,i=2,o=.1){let a=i*2+1;if(o=Math.min(e/2,t/2,s/2,o),super(1,1,1,a,a,a),this.type="RoundedBoxGeometry",this.parameters={width:e,height:t,depth:s,segments:i,radius:o},a===1)return;let n=this.toNonIndexed();this.index=null,this.attributes.position=n.attributes.position,this.attributes.normal=n.attributes.normal,this.attributes.uv=n.attributes.uv;let r=new tt,l=new tt,h=new tt(e,t,s).divideScalar(2).subScalar(o),u=this.attributes.position.array,d=this.attributes.normal.array,f=this.attributes.uv.array,p=u.length/6,g=new tt,v=.5/a;for(let m=0,x=0;m<u.length;m+=3,x+=2)switch(r.fromArray(u,m),l.copy(r),l.x-=Math.sign(l.x)*v,l.y-=Math.sign(l.y)*v,l.z-=Math.sign(l.z)*v,l.normalize(),u[m+0]=h.x*Math.sign(r.x)+l.x*o,u[m+1]=h.y*Math.sign(r.y)+l.y*o,u[m+2]=h.z*Math.sign(r.z)+l.z*o,d[m+0]=l.x,d[m+1]=l.y,d[m+2]=l.z,Math.floor(m/p)){case 0:g.set(1,0,0),f[x+0]=ae(g,l,"z","y",o,s),f[x+1]=1-ae(g,l,"y","z",o,t);break;case 1:g.set(-1,0,0),f[x+0]=1-ae(g,l,"z","y",o,s),f[x+1]=1-ae(g,l,"y","z",o,t);break;case 2:g.set(0,1,0),f[x+0]=1-ae(g,l,"x","z",o,e),f[x+1]=ae(g,l,"z","x",o,s);break;case 3:g.set(0,-1,0),f[x+0]=1-ae(g,l,"x","z",o,e),f[x+1]=1-ae(g,l,"z","x",o,s);break;case 4:g.set(0,0,1),f[x+0]=1-ae(g,l,"x","y",o,e),f[x+1]=1-ae(g,l,"y","x",o,t);break;case 5:g.set(0,0,-1),f[x+0]=ae(g,l,"x","y",o,e),f[x+1]=1-ae(g,l,"y","x",o,t);break}}static fromJSON(e){return new c(e.width,e.height,e.depth,e.segments,e.radius)}};import{BufferAttribute as Ii,BufferGeometry as Ni,Float32BufferAttribute as pa,InstancedBufferAttribute as fa,InterleavedBuffer as ma,InterleavedBufferAttribute as ga,MathUtils as va,TriangleFanDrawMode as xa,TriangleStripDrawMode as ba,TrianglesDrawMode as wa,Vector3 as Ma}from"three";function Fe(c,e=!1){let t=c[0].index!==null,s=new Set(Object.keys(c[0].attributes)),i=new Set(Object.keys(c[0].morphAttributes)),o={},a={},n=c[0].morphTargetsRelative,r=new Ni;r.morphTargetsRelative=n;let l=0;for(let h=0;h<c.length;++h){let u=c[h],d=0,f=0;if(t!==(u.index!==null))return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+". All geometries must have compatible attributes; make sure index attribute exists among all geometries, or in none of them."),null;for(let p in u.attributes){if(!s.has(p))return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+'. All geometries must have compatible attributes; make sure "'+p+'" attribute exists among all geometries, or in none of them.'),null;o[p]===void 0&&(o[p]=[]),o[p].push(u.attributes[p]),d++}if(d!==s.size)return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+". Make sure all geometries have the same number of attributes."),null;if(n!==u.morphTargetsRelative)return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+". .morphTargetsRelative must be consistent throughout all geometries."),null;for(let p in u.morphAttributes){if(!i.has(p))return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+".  .morphAttributes must be consistent throughout all geometries."),null;if(u.morphAttributes[p].length!==c[0].morphAttributes[p].length)return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+'. All geometries must have the same number of morph targets for the "'+p+'" morph attribute.'),null;a[p]===void 0&&(a[p]=[]),a[p].push(u.morphAttributes[p]),f++}if(f!==i.size)return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+". Make sure all geometries have the same number of morph attributes."),null;if(e){let p;if(t)p=u.index.count;else if(u.attributes.position!==void 0)p=u.attributes.position.count;else return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed with geometry at index "+h+". The geometry must have either an index or a position attribute"),null;r.addGroup(l,p,h),l+=p}}if(t){let h=0,u=[];for(let d=0;d<c.length;++d){let f=c[d].index;for(let p=0;p<f.count;++p)u.push(f.getX(p)+h);h+=c[d].attributes.position.count}r.setIndex(u)}for(let h in o){let u=js(o[h]);if(!u)return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed while trying to merge the "+h+" attribute."),null;r.setAttribute(h,u)}for(let h in a){let u=a[h][0].length;if(u!==0){r.morphAttributes=r.morphAttributes||{},r.morphAttributes[h]=[];for(let d=0;d<u;++d){let f=[];for(let g=0;g<a[h].length;++g)f.push(a[h][g][d]);let p=js(f);if(!p)return console.error("THREE.BufferGeometryUtils: .mergeGeometries() failed while trying to merge the "+h+" morphAttribute."),null;r.morphAttributes[h].push(p)}}}return r}function js(c){let e,t,s,i=-1,o=0;for(let l=0;l<c.length;++l){let h=c[l];if(e===void 0&&(e=h.array.constructor),e!==h.array.constructor)return console.error("THREE.BufferGeometryUtils: .mergeAttributes() failed. BufferAttribute.array must be of consistent array types across matching attributes."),null;if(t===void 0&&(t=h.itemSize),t!==h.itemSize)return console.error("THREE.BufferGeometryUtils: .mergeAttributes() failed. BufferAttribute.itemSize must be consistent across matching attributes."),null;if(s===void 0&&(s=h.normalized),s!==h.normalized)return console.error("THREE.BufferGeometryUtils: .mergeAttributes() failed. BufferAttribute.normalized must be consistent across matching attributes."),null;if(i===-1&&(i=h.gpuType),i!==h.gpuType)return console.error("THREE.BufferGeometryUtils: .mergeAttributes() failed. BufferAttribute.gpuType must be consistent across matching attributes."),null;o+=h.count*t}let a=new e(o),n=new Ii(a,t,s),r=0;for(let l=0;l<c.length;++l){let h=c[l];if(h.isInterleavedBufferAttribute){let u=r/t;for(let d=0,f=h.count;d<f;d++)for(let p=0;p<t;p++){let g=h.getComponent(d,p);n.setComponent(d+u,p,g)}}else a.set(h.array,r);r+=h.count*t}return i!==void 0&&(n.gpuType=i),n}import{rng as Ui}from"kimi/game";import*as B from"three";var Ys=`
float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vnoise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1,0)), f.x), mix(h21(i + vec2(0,1)), h21(i + vec2(1,1)), f.x), f.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 4; i++){ v += a * vnoise(p); p *= 2.03; a *= 0.5; } return v; }
`,st={uNight:{value:0},uTime:{value:0}};function Ks(c,e){c.vertexShader=c.vertexShader.replace("#include <common>",`#include <common>
varying vec3 vWPos;
varying vec3 vWNrm;
varying float vSeed;
varying float vH;`).replace("#include <worldpos_vertex>",`#include <worldpos_vertex>
      {
        mat4 m = modelMatrix;
        ${e?`#ifdef USE_INSTANCING
 m = modelMatrix * instanceMatrix;
#endif`:""}
        vWPos = (m * vec4(transformed, 1.0)).xyz;
        vWNrm = normalize(mat3(m) * objectNormal);
        vSeed = fract(sin(dot(m[3].xz, vec2(12.9898, 78.233))) * 43758.5453);
        vH = length(m[1].xyz);
      }`)}function $s(c){let e=new Uint8Array(N*N*4);for(let i=0;i<N;i++)for(let o=0;o<N;o++){let a=(o*N+i)*4;e[a]=c.use[i][o]*16,e[a+3]=255}let t=new B.DataTexture(e,N,N,B.RGBAFormat);t.magFilter=t.minFilter=B.NearestFilter,t.needsUpdate=!0;let s=new B.MeshStandardMaterial({color:16777215,roughness:.85,metalness:0});return s.onBeforeCompile=i=>{i.uniforms.uBlocks={value:t},i.uniforms.uNight=st.uNight,Ks(i,!1),i.fragmentShader=i.fragmentShader.replace("#include <common>",`#include <common>
        varying vec3 vWPos; varying vec3 vWNrm; varying float vSeed; varying float vH;
        uniform sampler2D uBlocks; uniform float uNight;
        ${Ys}
        const float HALF = ${X.toFixed(1)}; const float PITCH = ${te.toFixed(1)};
        float gRough = 0.85;
        vec3 groundCol(vec2 p) {
          float fx = mod(p.x + HALF, PITCH), fz = mod(p.y + HALF, PITCH);
          float dx = min(fx, PITCH - fx), dz = min(fz, PITCH - fz);
          bool outside = abs(p.x) > HALF + 7.0 || abs(p.y) > HALF + 7.0;
          bool rz = dx < 7.0 && abs(p.y) < HALF + 7.0, rx = dz < 7.0 && abs(p.x) < HALF + 7.0;
          float n = fbm(p * 0.35), n2 = vnoise(p * 3.1);
          if (outside && !rz && !rx) {           // dry grass and red soil around the city
            gRough = 1.0;
            return mix(vec3(0.20, 0.16, 0.08), vec3(0.18, 0.22, 0.08), smoothstep(0.35, 0.65, fbm(p * 0.05))) * (0.75 + 0.5 * n);
          }
          if (rz || rx) {                         // asphalt
            vec3 c = vec3(0.055, 0.057, 0.062) * (0.78 + 0.45 * n) + 0.02 * n2;
            c = mix(c, vec3(0.035), smoothstep(0.6, 0.75, fbm(p * 0.08 + 7.0)) * 0.6); // patches
            gRough = 0.78 + 0.2 * n2;
            bool inter = rz && rx;
            float paint = 0.0;
            if (rz && !inter) {
              paint += step(dx, 0.12) * step(mod(p.y, 9.0), 4.5);      // dashed centre line
              paint += step(abs(dx - 6.3), 0.1);                       // edge lines
              if (dz > 7.6 && dz < 10.6 && dx < 6.0) paint += step(mod(p.x, 1.3), 0.65); // zebra
            }
            if (rx && !inter) {
              paint += step(dz, 0.12) * step(mod(p.x, 9.0), 4.5);
              paint += step(abs(dz - 6.3), 0.1);
              if (dx > 7.6 && dx < 10.6 && dz < 6.0) paint += step(mod(p.y, 1.3), 0.65);
            }
            paint = clamp(paint, 0.0, 1.0) * (0.7 + 0.3 * vnoise(p * 5.0)); // worn paint
            gRough = mix(gRough, 0.55, paint);
            return mix(c, vec3(0.62, 0.62, 0.58), paint);
          }
          if (dx < 11.0 || dz < 11.0) {           // sidewalks with a stone curb
            float d = min(dx, dz);
            if (d < 7.4) { gRough = 0.7; return vec3(0.36, 0.35, 0.33) * (0.9 + 0.2 * n2); }
            vec2 t = fract(p / 1.5);
            float grout = step(t.x, 0.04) + step(t.y, 0.04);
            gRough = 0.8;
            vec3 c = vec3(0.27, 0.26, 0.245) * (0.85 + 0.3 * h21(floor(p / 1.5))) * (0.9 + 0.2 * n);
            return mix(c, vec3(0.12), clamp(grout, 0.0, 1.0) * 0.8);
          }
          // inside a block: what kind?
          vec2 bi = floor((p + HALF) / PITCH);
          float use = floor(texture2D(uBlocks, (bi + 0.5) / ${N.toFixed(1)}).r * 255.0 / 16.0 + 0.5);
          vec2 lp = p - (-HALF + bi * PITCH + PITCH * 0.5);
          if (abs(use - 2.0) < 0.5) {             // park: grass with paths
            float path = step(abs(lp.x), 1.6) + step(abs(lp.y), 1.6) + step(abs(length(lp) - 12.0), 1.5);
            vec3 grass = mix(vec3(0.07, 0.17, 0.04), vec3(0.16, 0.24, 0.06), fbm(p * 0.2)) * (0.8 + 0.4 * n2);
            gRough = mix(0.95, 0.85, clamp(path, 0.0, 1.0));
            return mix(grass, vec3(0.34, 0.27, 0.2) * (0.85 + 0.3 * n2), clamp(path, 0.0, 1.0));
          }
          if (abs(use - 3.0) < 0.5) {             // plaza paving
            vec2 t = fract(lp / 2.0);
            float g = step(t.x, 0.03) + step(t.y, 0.03);
            float ring = step(abs(length(lp) - 9.0), 0.6) + step(abs(length(lp) - 15.0), 0.4);
            vec3 c = mix(vec3(0.36, 0.30, 0.25), vec3(0.42, 0.40, 0.36), h21(floor(lp / 2.0))) * (0.9 + 0.2 * n);
            c = mix(c, vec3(0.55, 0.22, 0.12), clamp(ring, 0.0, 1.0) * 0.7);
            gRough = 0.75;
            return mix(c, vec3(0.15), clamp(g, 0.0, 1.0) * 0.6);
          }
          // concrete yards and alleys, with joints and oil stains
          vec2 t = fract(lp / 4.0);
          float j = step(t.x, 0.015) + step(t.y, 0.015);
          vec3 c = vec3(0.28, 0.28, 0.27) * (0.8 + 0.35 * n);
          c *= 1.0 - smoothstep(0.62, 0.8, fbm(p * 0.3 + 3.0)) * 0.5;
          gRough = 0.9;
          return mix(c, vec3(0.1), clamp(j, 0.0, 1.0) * 0.7);
        }`).replace("#include <map_fragment>",`#include <map_fragment>
        diffuseColor.rgb *= groundCol(vWPos.xz);`).replace("#include <roughnessmap_fragment>",`#include <roughnessmap_fragment>
        roughnessFactor = gRough;`)},s.customProgramCacheKey=()=>"nyota-ground",s}var Xs={glass:{ww:2.2,fh:3.8,frame:.06,shop:0,wall:[.16,.2,.24],glassTint:[.1,.16,.2],mullion:1},concrete:{ww:3,fh:3.6,frame:.18,shop:1,wall:[.55,.53,.5],glassTint:[.06,.08,.1],mullion:0},plaster:{ww:3.4,fh:3.4,frame:.24,shop:1,wall:[.7,.62,.5],glassTint:[.05,.07,.08],mullion:0},brick:{ww:3.2,fh:3.5,frame:.22,shop:0,wall:[.45,.22,.14],glassTint:[.05,.07,.08],mullion:0}};function Me(c,e={}){let t=Xs[c],s=new B.MeshStandardMaterial({color:16777215,roughness:.8,metalness:0,envMapIntensity:1});return s.onBeforeCompile=i=>{i.uniforms.uNight=st.uNight,Ks(i,!0),i.fragmentShader=i.fragmentShader.replace("#include <common>",`#include <common>
        varying vec3 vWPos; varying vec3 vWNrm; varying float vSeed; varying float vH;
        uniform float uNight;
        ${Ys}
        float fRough = 0.8, fMetal = 0.0; vec3 fEmit = vec3(0.0);
        vec3 facade(vec3 base) {
          vec3 n = normalize(vWNrm);
          if (n.y > 0.5) { fRough = 0.95; return vec3(0.22, 0.22, 0.21) * (0.8 + 0.4 * vnoise(vWPos.xz * 1.7)); }   // roof
          if (n.y < -0.5) { return base * 0.4; }
          float u = abs(n.x) > 0.5 ? vWPos.z : vWPos.x;
          float v = vWPos.y;
          float H = ${e.height?e.height.toFixed(2):"vH"};
          const float WW = ${t.ww.toFixed(2)}; const float FH = ${t.fh.toFixed(2)}; const float FR = ${t.frame.toFixed(2)};
          vec2 cell = vec2(floor(u / WW), floor(v / FH));
          vec2 c = vec2(fract(u / WW), fract(v / FH));
          float grime = 0.85 + 0.3 * vnoise(vec2(u, v) * 0.15) - 0.15 * smoothstep(1.5, 0.0, v);
          vec3 wall = base * grime;
          if (v > H - 0.7) { fRough = 0.8; return wall * 0.75; }                       // parapet
          bool shopFloor = ${t.shop?"v < 4.4":"false"};
          if (shopFloor) {
            if (v > 3.7) { fRough = 0.6; return mix(vec3(0.6, 0.12, 0.08), vec3(0.1, 0.3, 0.55), step(0.5, vSeed)) * 0.8; } // signboard band
            float sc = fract(u / 6.0);
            if (v > 0.15 && v < 3.4 && sc > 0.06 && sc < 0.94) {                      // shop window
              fRough = 0.05; fMetal = 0.9;
              float lit = step(0.35, h21(vec2(floor(u / 6.0), vSeed * 91.0)));
              fEmit = vec3(1.0, 0.78, 0.5) * lit * (0.08 + 0.55 * uNight);
              return vec3(${t.glassTint.map(o=>o.toFixed(3)).join(",")});
            }
            fRough = 0.7;
            return wall * 0.6;
          }
          ${t.mullion?`
          // glass tower: continuous glass with thin mullions and floor bands
          float mull = step(c.x, FR) + step(abs(c.y - 0.02), 0.03);
          if (mull > 0.5) { fRough = 0.4; fMetal = 0.6; return vec3(0.18, 0.2, 0.22); }
          fRough = 0.03; fMetal = 1.0;
          float lit = step(0.76, h21(cell + vSeed * 37.0));
          fEmit = mix(vec3(1.0, 0.85, 0.6), vec3(0.7, 0.85, 1.0), h21(cell.yx + 3.0)) * lit * uNight * 0.5;
          return vec3(${t.glassTint.map(o=>o.toFixed(3)).join(",")}) * (0.8 + 0.4 * h21(cell * 1.3));`:`
          bool win = c.x > FR && c.x < 1.0 - FR && c.y > 0.3 && c.y < 0.86 && v > 1.2;
          if (win) {
            float sill = step(c.y, 0.33);
            if (sill > 0.5) { fRough = 0.6; return vec3(0.75); }
            fRough = 0.06; fMetal = 0.85;
            float lit = step(0.66, h21(cell + vSeed * 37.0));
            float curtain = step(0.7, h21(cell * 2.1 + 9.0));
            fEmit = mix(vec3(1.0, 0.75, 0.45), vec3(0.75, 0.85, 1.0), step(0.8, h21(cell.yx))) * lit * uNight * (curtain > 0.5 ? 0.25 : 0.55);
            return mix(vec3(${t.glassTint.map(o=>o.toFixed(3)).join(",")}), vec3(0.5, 0.45, 0.38), curtain * 0.6);
          }
          ${c==="brick"?`
          vec2 b = vec2(u * 4.0 + step(1.0, mod(floor(v * 12.0), 2.0)) * 0.5, v * 12.0);
          float mortar = step(fract(b.x), 0.06) + step(fract(b.y), 0.1);
          float bfar = smoothstep(0.15, 0.5, max(fwidth(b.x), fwidth(b.y))); // too small to draw: blend to the average
          vec3 brick = mix(wall * (0.8 + 0.4 * h21(floor(b))), vec3(0.42, 0.4, 0.36), clamp(mortar, 0.0, 1.0));
          wall = mix(brick, mix(wall, vec3(0.42, 0.4, 0.36), 0.15), bfar);`:""}
          fRough = 0.85;
          return wall;`}
        }
        // far away: the average of wall and glass, so distant towers don't shimmer
        vec3 facadeAA(vec3 base) {
          vec3 n = normalize(vWNrm);
          vec3 c = facade(base);
          if (abs(n.y) > 0.5) return c;
          float u = abs(n.x) > 0.5 ? vWPos.z : vWPos.x;
          float fw = max(fwidth(u / ${t.ww.toFixed(2)}), fwidth(vWPos.y / ${t.fh.toFixed(2)}));
          float far = smoothstep(0.07, 0.28, fw);
          if (far <= 0.0) return c;
          vec3 glass = vec3(${t.glassTint.map(o=>o.toFixed(3)).join(",")});
          vec3 avg = mix(base * 0.9, glass, ${t.mullion?"0.8":"0.4"});
          fEmit = mix(fEmit, vec3(1.0, 0.82, 0.55) * uNight * ${t.mullion?"0.12":"0.13"}, far);
          fRough = mix(fRough, ${t.mullion?"0.15":"0.5"}, far);
          fMetal = mix(fMetal, ${t.mullion?"0.8":"0.3"}, far);
          return mix(c, avg, far);
        }`).replace("#include <color_fragment>",`#include <color_fragment>
        diffuseColor.rgb = facadeAA(diffuseColor.rgb);`).replace("#include <roughnessmap_fragment>",`#include <roughnessmap_fragment>
        roughnessFactor = fRough;`).replace("#include <metalnessmap_fragment>",`#include <metalnessmap_fragment>
        metalnessFactor = fMetal;`).replace("#include <emissivemap_fragment>",`#include <emissivemap_fragment>
        totalEmissiveRadiance += fEmit;`)},s.customProgramCacheKey=()=>"nyota-facade-"+c+(e.height??""),e.height&&(s.color=new B.Color(...t.wall)),s}var Qs=Object.fromEntries(Object.entries(Xs).map(([c,e])=>[c,e.wall])),Ws=new Map;function se(c,e){let t=Ws.get(c);return t||(t=e(),Ws.set(c,t)),t}var L={concrete:()=>se("concrete",()=>new B.MeshStandardMaterial({color:9078658,roughness:.9})),darkConcrete:()=>se("dconcrete",()=>new B.MeshStandardMaterial({color:4868166,roughness:.95})),plaster:()=>se("plaster",()=>new B.MeshStandardMaterial({color:11050892,roughness:.9})),metal:()=>se("metal",()=>new B.MeshStandardMaterial({color:9212054,roughness:.35,metalness:.9})),darkMetal:()=>se("dmetal",()=>new B.MeshStandardMaterial({color:2763824,roughness:.45,metalness:.8})),wood:()=>se("wood",()=>new B.MeshStandardMaterial({color:9067059,roughness:.75})),rubber:()=>se("rubber",()=>new B.MeshStandardMaterial({color:1315860,roughness:.9})),glass:()=>se("glass",()=>new B.MeshPhysicalMaterial({color:726040,roughness:.05,metalness:.2,transmission:0,transparent:!0,opacity:.55,envMapIntensity:1.6})),foliage:()=>se("foliage",()=>new B.MeshStandardMaterial({color:3104543,roughness:.85,flatShading:!0})),trunk:()=>se("trunk",()=>new B.MeshStandardMaterial({color:4863268,roughness:.95})),lampOn:()=>se("lamp",()=>{let c=new B.MeshStandardMaterial({color:2236962,emissive:16767392,emissiveIntensity:0});return c.userData.night=6,c}),tile:()=>se("tile",()=>{let c=new B.MeshStandardMaterial({color:16777215,roughness:.35});return c.map=le(256,256,(e,t,s)=>{e.fillStyle="#b9b2a6",e.fillRect(0,0,t,s);for(let i=0;i<4;i++)for(let o=0;o<4;o++){let a=160+(o+i)%2*30+Math.random()*10;e.fillStyle=`rgb(${a},${a-6},${a-14})`,e.fillRect(o*64+2,i*64+2,60,60)}},4),c})};function le(c,e,t,s=1){let i=document.createElement("canvas");i.width=c,i.height=e,t(i.getContext("2d"),c,e);let o=new B.CanvasTexture(i);return o.colorSpace=B.SRGBColorSpace,o.wrapS=o.wrapT=B.RepeatWrapping,o.repeat.set(s,s),o.anisotropy=8,o}var ws=[["NYOTA COLA","ice cold since 1963","#c81e2b","#fff"],["MATATU EXPRESS","every route \xB7 every day","#1b9e5a","#ffe14d"],["SIMBA DATA","4G everywhere you roam","#ffb300","#1a1a1a"],["KIMI OS","talk to your computer","#101828","#36e2ff"],["MAZIWA FRESH","milk from the highlands","#f4f1ea","#1f6fb2"],["BODA SAFE","helmets save lives","#ff5a1f","#fff"]];function Zs(c){let[e,t,s,i]=ws[c%ws.length];return le(512,256,(o,a,n)=>{let r=o.createLinearGradient(0,0,a,n);r.addColorStop(0,s),r.addColorStop(1,Bi(s,-.25)),o.fillStyle=r,o.fillRect(0,0,a,n),o.fillStyle=i,o.globalAlpha=.15;for(let l=0;l<6;l++)o.beginPath(),o.arc(a*.85,n*.5,30+l*28,0,Math.PI*2),o.lineWidth=8,o.strokeStyle=i,o.stroke();o.globalAlpha=1,o.font="900 64px system-ui, sans-serif",o.fillText(e,28,118),o.font="500 28px system-ui, sans-serif",o.fillText(t,30,170)})}var Js=ws.length;function Bi(c,e){let t=new B.Color(c);return t.offsetHSL(0,0,e),"#"+t.getHexString()}var qs=["NYOTA","MOTO MOTO","BARAKA","SAFARI","TUKO PAMOJA","KASI"];function ei(c){let e=[["#ffd60a","#e63946","#1d3557"],["#06d6a0","#118ab2","#073b4c"],["#ff006e","#8338ec","#ffbe0b"],["#f77f00","#fcbf49","#003049"]],[t,s,i]=e[c%e.length];return le(512,128,(o,a,n)=>{o.fillStyle=t,o.fillRect(0,0,a,n),o.fillStyle=s,o.beginPath(),o.moveTo(0,n*.55),o.bezierCurveTo(a*.3,n*.2,a*.6,n*.95,a,n*.45),o.lineTo(a,n),o.lineTo(0,n),o.fill(),o.fillStyle=i,o.fillRect(0,n*.88,a,n*.12),o.font="italic 900 54px system-ui, sans-serif",o.fillStyle="#fff",o.strokeStyle=i,o.lineWidth=6;let r=qs[c%qs.length];o.strokeText(r,30,74),o.fillText(r,30,74)})}function Ms(){return se("soft",()=>le(64,64,(c,e,t)=>{let s=c.createRadialGradient(e/2,t/2,0,e/2,t/2,e/2);s.addColorStop(0,"rgba(255,255,255,1)"),s.addColorStop(.4,"rgba(255,255,255,0.55)"),s.addColorStop(1,"rgba(255,255,255,0)"),c.fillStyle=s,c.fillRect(0,0,e,t)}))}var ys=class{constructor(){this.parts=new Map}part(e){let t=this.parts.get(e);return t||(t={pos:[],nrm:[],uv:[],idx:[]},this.parts.set(e,t)),t}quad(e,t,s,i,o,a,n=2){let r=this.part(e),l=r.pos.length/3;for(let h of[t,s,i,o])r.pos.push(h[0],h[1],h[2]),r.nrm.push(a[0],a[1],a[2]),Math.abs(a[0])>.5?r.uv.push(h[2]/n,h[1]/n):Math.abs(a[1])>.5?r.uv.push(h[0]/n,h[2]/n):r.uv.push(h[0]/n,h[1]/n);r.idx.push(l,l+1,l+2,l,l+2,l+3)}box(e,t,s,i,o,a,n,r=2){let l=typeof n=="string"?{px:n,nx:n,py:n,ny:n,pz:n,nz:n}:n;l.px&&this.quad(l.px,[i,t,a],[i,t,s],[i,o,s],[i,o,a],[1,0,0],r),l.nx&&this.quad(l.nx,[e,t,s],[e,t,a],[e,o,a],[e,o,s],[-1,0,0],r),l.py&&this.quad(l.py,[e,o,a],[i,o,a],[i,o,s],[e,o,s],[0,1,0],r),l.ny&&this.quad(l.ny,[e,t,s],[i,t,s],[i,t,a],[e,t,a],[0,-1,0],r),l.pz&&this.quad(l.pz,[e,t,a],[i,t,a],[i,o,a],[e,o,a],[0,0,1],r),l.nz&&this.quad(l.nz,[i,t,s],[e,t,s],[e,o,s],[i,o,s],[0,0,-1],r)}slope(e,t,s,i,o,a=2){let n=new y.Vector3(...s).sub(new y.Vector3(...t)),r=new y.Vector3(...o).sub(new y.Vector3(...t)),l=new y.Vector3().crossVectors(r,n).normalize();this.quad(e,t,s,i,o,[l.x,l.y,l.z],a)}meshes(e){let t=[];for(let[s,i]of this.parts){let o=new y.BufferGeometry;o.setAttribute("position",new y.Float32BufferAttribute(i.pos,3)),o.setAttribute("normal",new y.Float32BufferAttribute(i.nrm,3)),o.setAttribute("uv",new y.Float32BufferAttribute(i.uv,2)),o.setIndex(i.idx),o.computeBoundingSphere();let a=new y.Mesh(o,e[s]??L.concrete());a.castShadow=a.receiveShadow=!0,a.name=s,t.push(a)}return t}},He=class{constructor(e,t,s={}){this.geometry=e,this.material=t,this.list=[],this.opts=s}add(e,t,s,i=1,o=1,a=1,n=0,r=null){this.list.push({x:e,y:t,z:s,sx:i,sy:o,sz:a,rotY:n,color:r})}build(){let e=new y.InstancedMesh(this.geometry,this.material,Math.max(1,this.list.length)),t=new y.Matrix4,s=new y.Quaternion,i=new y.Euler;return this.list.forEach((o,a)=>{s.setFromEuler(i.set(0,o.rotY,0)),t.compose(new y.Vector3(o.x,o.y,o.z),s,new y.Vector3(o.sx,o.sy,o.sz)),e.setMatrixAt(a,t),o.color&&e.setColorAt(a,new y.Color(...o.color))}),e.count=this.list.length,e.castShadow=this.opts.shadow!==!1,e.receiveShadow=!0,e.computeBoundingSphere(),e}},it=()=>{let c=new y.BoxGeometry(1,1,1);return c.translate(0,.5,0),c},Tt=class{constructor(e,t,s,i){this.g=e,this.scene=t,this.plan=s,this.sky=i,this.root=new y.Group,this.three=e.three.scene,this.b=new ys,this.r=Ui(s.seed),this.surfaces=[],this.covers=[],this.interiors=[],this.spots={},this.pickupSpots=[],this.parking=[],this.sidewalkLoops=[],this.lampLights=[],this.colliders=[],this.inst={glass:new He(it(),Me("glass")),concrete:new He(it(),Me("concrete")),plaster:new He(it(),Me("plaster")),brick:new He(it(),Me("brick"))},this.props={},this.build()}solid(e,t,s,i,o,a,n=["wall"]){let r=i-e,l=o-t,h=a-s;if(r<=0||l<=0||h<=0)return null;let u=this.scene.add({at:[(e+i)/2,(t+o)/2,(s+a)/2],box:[r,l,h],tags:n,solid:!0,static:!0,hidden:!0});return this.colliders.push({x0:e,y0:t,z0:s,x1:i,y1:o,z1:a,e:u}),l>=.9&&t<.5&&r<14&&h<14&&this.addCover(e,s,i,a),u}slab(e,t,s,i,o,a){return this.scene.add({at:[(e+i)/2,(t+o)/2,(s+a)/2],box:[i-e,o-t,a-s],tags:["prop","slab"],static:!0,hidden:!0})}post(e,t,s,i=4,o=["prop"]){return this.scene.add({at:[e,i/2,t],radius:s,capsule:[s,i],height:i/2,tags:o,solid:!0,static:!0,hidden:!0})}addCover(e,t,s,i){let o=(e+s)/2,a=(t+i)/2,n=.7;this.covers.push([e-n,a,-1,0],[s+n,a,1,0],[o,t-n,0,-1],[o,i+n,0,1])}groundAt(e,t,s=0,i=.75){let o=0;for(let a of this.surfaces){if(e<a.x0||e>a.x1||t<a.z0||t>a.z1)continue;let n=a.y;if(a.ramp){let r=a.ramp==="z"?(t-a.z0)/(a.z1-a.z0):(e-a.x0)/(a.x1-a.x0);n=a.y0+(a.y1-a.y0)*Math.max(0,Math.min(1,r))}n<=s+i&&n>o&&(o=n)}return o}build(){let{three:e}=this,t=new y.Mesh(new y.PlaneGeometry(1600,1600,1,1),$s(this.plan));t.rotation.x=-Math.PI/2,t.receiveShadow=!0,e.add(t),this.ground=t;for(let i=0;i<N;i++)for(let o=0;o<N;o++)this.block(i,o,this.plan.use[i][o]);this.streets(),this.edges(),this.mountains();let s={concrete:L.concrete(),dark:L.darkConcrete(),plaster:L.plaster(),metal:L.metal(),darkMetal:L.darkMetal(),wood:L.wood(),tile:L.tile(),glass:L.glass(),shopFacade:Me("plaster",{height:5.2}),brickFacade:Me("brick",{height:8.5}),lobbyFacade:Me("concrete",{height:6.5}),lamp:L.lampOn(),ceilLamp:(()=>{let i=L.lampOn().clone();return i.userData.night=3,i.emissive.set(16773590),i})(),corrugated:new y.MeshStandardMaterial({color:10133670,roughness:.5,metalness:.7,map:le(64,64,(i,o,a)=>{for(let n=0;n<o;n+=8){let r=i.createLinearGradient(n,0,n+8,0);r.addColorStop(0,"#777"),r.addColorStop(.5,"#ddd"),r.addColorStop(1,"#777"),i.fillStyle=r,i.fillRect(n,0,8,a)}})})};this.sky.lamps.push(s.lamp,s.ceilLamp);for(let i of this.b.meshes(s))e.add(i);for(let[i,o]of Object.entries(this.inst)){let a=o.build();a.name="towers-"+i,e.add(a)}for(let[i,o]of Object.entries(this.props)){let a=o.build();a.name="props-"+i,e.add(a)}this.buildNav()}prop(e,t,s,i){return this.props[e]||(this.props[e]=new He(t(),typeof s=="function"?s():s,i)),this.props[e]}tower(e,t,s,i,o,a,n=0){let r=s-e,l=i-t,h=Qs[a],u=.85+this.r.next()*.3,d=a==="glass"?[h[0]*u,h[1]*u,h[2]*u]:h.map(f=>Math.min(1,f*u));if(this.inst[a].add((e+s)/2,n,(t+i)/2,r,o,l,0,d),this.solid(e,n,t,s,n+o,i,["wall","building"]),o<45&&r>8&&l>8){let f=this.prop("ac",()=>{let g=new fe(1,1,1,2,.08);return g.translate(0,.5,0),g},L.metal()),p=1+Math.floor(this.r.next()*3);for(let g=0;g<p;g++)f.add(e+2+this.r.next()*(r-4),n+o,t+2+this.r.next()*(l-4),1.6,1.1,1.2);this.r.next()<.45&&this.prop("tank",()=>{let v=new y.CylinderGeometry(.9,.9,1.8,14);return v.translate(0,.9,0),v},new y.MeshStandardMaterial({color:2042422,roughness:.6})).add(e+3,n+o,i-3)}else o>=45&&(this.prop("antenna",()=>{let g=new y.CylinderGeometry(.08,.15,1,6);return g.translate(0,.5,0),g},L.darkMetal()).add((e+s)/2,n+o,(t+i)/2,1,6+this.r.next()*8,1),this.prop("beacon",()=>new y.SphereGeometry(.25,8,6),()=>{let g=new y.MeshStandardMaterial({color:3342336,emissive:16719904,emissiveIntensity:0});return g.userData.night=8,this.sky.lamps.push(g),g},{shadow:!1}).add((e+s)/2,n+o+14,(t+i)/2));o>10&&o<40&&this.r.next()<.18&&this.billboard((e+s)/2,n+o,t+1,Math.floor(this.r.next()*Js))}billboard(e,t,s,i){let o=new y.Group,a=new y.Mesh(new y.PlaneGeometry(10,5),new y.MeshStandardMaterial({map:Zs(i),emissive:16777215,emissiveMap:null,emissiveIntensity:0,roughness:.5}));a.material.emissiveMap=a.material.map,a.material.userData.night=.9,this.sky.lamps.push(a.material),a.position.set(0,4.5,0),a.rotation.y=Math.PI;let n=new y.Mesh(new y.BoxGeometry(10.4,5.4,.3),L.darkMetal());n.position.set(0,4.5,.2);let r=new y.Mesh(new y.BoxGeometry(.3,2,.3),L.darkMetal());r.position.set(-3,1,.2);let l=r.clone();l.position.x=3,o.add(a,n,r,l),o.position.set(e,t,s),o.traverse(h=>{h.castShadow=!0}),this.three.add(o)}room(e,t,s,i,o,a,n={}){let l=n.facade??"shopFacade",h=n.inside??"plaster",u={s:{a:[e,t],b:[s,t],out:"nz"},n:{a:[e,i],b:[s,i],out:"pz"},w:{a:[e,t],b:[e,i],out:"nx"},e:{a:[s,t],b:[s,i],out:"px"}};for(let[v,m]of Object.entries(u)){let x=v==="s"||v==="n",b=x?s-e:i-t,w=a.filter(T=>T.side===v).map(T=>[b/2+(T.at??0)-T.width/2,b/2+(T.at??0)+T.width/2,T]);w.sort((T,R)=>T[0]-R[0]);let M=[],E=0;for(let[T,R]of w)T>E&&M.push([E,T]),E=R;E<b&&M.push([E,b]);let S=T=>({px:T==="px"?l:h,nx:T==="nx"?l:h,pz:T==="pz"?l:h,nz:T==="nz"?l:h,py:"dark",ny:null});for(let[T,R]of M){let z,k,P,_;if(x?(z=m.a[0]+T,P=m.a[0]+R,k=m.a[1]-.35/2,_=m.a[1]+.35/2):(k=m.a[1]+T,_=m.a[1]+R,z=m.a[0]-.35/2,P=m.a[0]+.35/2),n.glassSide===v){this.b.box(z,0,k,P,o,_,{px:"glass",nx:"glass",pz:"glass",nz:"glass",py:"dark"});for(let oe=0;oe<=(R-T)/3;oe++){let ee=T+oe*3;x?this.b.box(m.a[0]+ee-.08,0,k-.05,m.a[0]+ee+.08,o,_+.05,"darkMetal"):this.b.box(z-.05,0,m.a[1]+ee-.08,P+.05,o,m.a[1]+ee+.08,"darkMetal")}}else this.b.box(z,0,k,P,o,_,S(m.out));this.solid(z,0,k,P,o,_,["wall"])}for(let[T,R,z]of w){let k=z.height??Math.min(o,z.width>4?4.6:2.8);k<o&&(x?this.b.box(m.a[0]+T,k,m.a[1]-.35/2,m.a[0]+R,o,m.a[1]+.35/2,S(m.out)):this.b.box(m.a[0]-.35/2,k,m.a[1]+T,m.a[0]+.35/2,o,m.a[1]+R,S(m.out))),z.width>4&&(x?this.b.box(m.a[0]+T,k-.6,m.a[1]-.35,m.a[0]+R,k,m.a[1]+.35,"corrugated"):this.b.box(m.a[0]-.35,k-.6,m.a[1]+T,m.a[0]+.35,k,m.a[1]+R,"corrugated"));let P=(T+R)/2,_=x?[m.a[0]+P,m.a[1]]:[m.a[0],m.a[1]+P];(this.doors??=[]).push(_)}}this.b.box(e,0,t,s,.03,i,{py:n.floor??"tile"},2),this.b.box(e-.35/2,o,t-.35/2,s+.35/2,o+.4,i+.35/2,{py:"dark",ny:"plaster",px:l,nx:l,pz:l,nz:l});let d=[],f=Math.max(1,Math.round((s-e)/8)),p=Math.max(1,Math.round((i-t)/8));for(let v=0;v<f;v++)for(let m=0;m<p;m++){let x=e+(v+.5)*(s-e)/f,b=t+(m+.5)*(i-t)/p;this.b.box(x-.6,o-.08,b-.25,x+.6,o,b+.25,{ny:"ceilLamp",px:"metal",nx:"metal",pz:"metal",nz:"metal"}),d.push([x,o-.4,b])}let g={x0:e,z0:t,x1:s,z1:i,h:o,lamps:d,name:n.name};return this.interiors.push(g),g}crate(e,t,s=1.2,i=1,o=["prop","cover"]){let a=this.prop("crate",()=>{let n=new fe(1,1,1,1,.04);return n.translate(0,.5,0),n},L.wood());for(let n=0;n<i;n++)a.add(e,n*s,t,s,s,s,(pe(e,t+n)-.5)*.4);this.solid(e-s/2,0,t-s/2,e+s/2,s*i,t+s/2,o)}container(e,t,s,i){let o=this.prop("container",()=>it(),()=>new y.MeshStandardMaterial({color:16777215,roughness:.55,metalness:.5,map:le(128,64,(r,l,h)=>{r.fillStyle="#ddd",r.fillRect(0,0,l,h);for(let u=0;u<l;u+=6)r.fillStyle=u%12?"#bbb":"#f2f2f2",r.fillRect(u,0,3,h)})})),[a,n]=s?[6,2.5]:[2.5,6];o.add(e,0,t,a,2.6,n,0,i),this.solid(e-a/2,0,t-n/2,e+a/2,2.6,t+n/2,["wall","cover"])}barrier(e,t,s){let i=this.prop("barrier",()=>{let n=new y.CylinderGeometry(.25,.4,.9,4,1);return n.rotateY(Math.PI/4),n.scale(1,1,1),n.translate(0,.45,0),n},L.concrete()),[o,a]=s?[2.2,.6]:[.6,2.2];i.add(e,0,t,o*1.4,1,a*1.4),this.solid(e-o/2,0,t-a/2,e+o/2,.95,t+a/2,["prop","cover"])}tree(e,t,s=1){this.prop("trunk",()=>{let a=new y.CylinderGeometry(.16,.26,1,7);return a.translate(0,.5,0),a},L.trunk()).add(e,0,t,s,3.2*s,s),this.prop("crown",()=>{let a=[];for(let r=0;r<3;r++){let l=new y.IcosahedronGeometry(1,1),h=l.attributes.position;for(let u=0;u<h.count;u++){let d=.8+.35*pe(u*7+r,u*3);h.setXYZ(u,h.getX(u)*d,h.getY(u)*d*.6,h.getZ(u)*d)}l.translate((r-1)*.9,r===1?.35:0,r%2*.6-.3),a.push(l.index?l.toNonIndexed():l)}let n=Fe(a);return n.computeVertexNormals(),n.translate(0,3.6,0),n},L.foliage()).add(e,0,t,s*1.25,s,s*1.25,pe(e,t)*6.28,[.75+pe(t,e)*.4,.85+pe(e+1,t)*.3,.7]),this.post(e,t,.3*s,3.5,["prop","tree"])}lamp(e,t,s){this.prop("lampPole",()=>{let a=new y.CylinderGeometry(.07,.11,7,8);a.translate(0,3.5,0);let n=new y.BoxGeometry(1.8,.1,.12);return n.translate(.85,7,0),Fe([a,n])},L.darkMetal()).add(e,0,t,1,1,1,s),this.prop("lampHead",()=>{let a=new y.BoxGeometry(.6,.12,.3);return a.translate(1.6,6.9,0),a},L.lampOn(),{shadow:!1}).add(e,0,t,1,1,1,s),this.lampLights.push([e+Math.cos(-s)*1.6,6.6,t+Math.sin(-s)*1.6]),this.post(e,t,.15,7,["prop","pole"])}bench(e,t,s){this.prop("bench",()=>{let o=new y.BoxGeometry(1.8,.08,.45);o.translate(0,.45,0);let a=new y.BoxGeometry(1.8,.4,.06);a.translate(0,.75,-.2);let n=new y.BoxGeometry(.08,.45,.45);n.translate(-.8,.22,0);let r=n.clone();return r.translate(1.6,0,0),Fe([o,a,n,r])},L.wood()).add(e,0,t,1,1,1,s)}kiosk(e,t,s,i){let o=new y.Group,a=new y.Mesh(new y.BoxGeometry(3,1.05,1.4),L.wood());a.position.y=.52;let n=new y.Mesh(new y.BoxGeometry(3.6,.06,2.6),new y.MeshStandardMaterial({color:i,roughness:.45,metalness:.6}));n.position.set(0,2.5,.2),n.rotation.x=.15,o.add(a,n);for(let l of[-1.6,1.6])for(let h of[-.9,1.2]){let u=new y.Mesh(new y.CylinderGeometry(.05,.05,2.5,6),L.darkMetal());u.position.set(l,1.25,h),o.add(u)}for(let l=0;l<6;l++){let h=new y.Mesh(new y.BoxGeometry(.3,.25,.3),new y.MeshStandardMaterial({color:new y.Color().setHSL(pe(e+l,t),.7,.5),roughness:.6}));h.position.set(-1.2+l*.48,1.18,-.2),o.add(h)}o.position.set(e,0,t),o.rotation.y=s,o.traverse(l=>{l.castShadow=l.receiveShadow=!0}),this.three.add(o);let r=Math.abs(Math.sin(s))>.5;this.solid(e-(r?.7:1.5),0,t-(r?1.5:.7),e+(r?.7:1.5),1.05,t+(r?1.5:.7),["prop","cover"])}block(e,t,s){let i=yt(e),o=yt(t),a=i-we/2,n=i+we/2,r=o-we/2,l=o+we/2,h=this.r,u=`${e},${t}`,d=p=>Ze[p][0]===e&&Ze[p][1]===t,f=te/2-Je+2;if(this.sidewalkLoops.push([[i-f,o-f],[i+f,o-f],[i+f,o+f],[i-f,o+f]]),s===D.towers)if(h.next()<.5){let g=a+we*(.4+h.next()*.2);this.tower(a,r,g-3,l,40+h.next()*60,h.pick(["glass","glass","concrete"])),this.tower(g+3,r,n,l,30+h.next()*50,h.pick(["glass","concrete"]))}else{let g=4+h.next()*6;this.tower(a+g,r+g,n-g,l-g,55+h.next()*50,"glass"),this.parking.push([a+2,r+1.5,0],[n-6,r+1.5,0])}else if(s===D.midrise||s===D.lowrise){let p=s===D.midrise?2:3,g=we/p;for(let v=0;v<p;v++)for(let m=0;m<p;m++){if(s===D.lowrise&&h.next()<.2){this.tree(a+(v+.5)*g,r+(m+.5)*g,1.2);continue}let x=s===D.midrise?2.5:3,b=s===D.midrise?12+h.next()*22:5+h.next()*6;this.tower(a+v*g+x,r+m*g+x,a+(v+1)*g-x,r+(m+1)*g-x,b,h.pick(s===D.midrise?["concrete","plaster","brick"]:["plaster","plaster","brick"]))}}else if(s===D.park){for(let p=0;p<14;p++){let g=h.next()*Math.PI*2,v=6+h.next()*18,m=i+Math.cos(g)*v,x=o+Math.sin(g)*v;Math.abs(m-i)<3||Math.abs(x-o)<3||Math.abs(Math.hypot(m-i,x-o)-12)<2.5||this.tree(m,x,.9+h.next()*.7)}for(let p=0;p<6;p++){let g=p/6*Math.PI*2;this.bench(i+Math.cos(g)*14.2,o+Math.sin(g)*14.2,-g-Math.PI/2)}this.fountain(i,o,3.5)}else s===D.plaza?this.plaza(i,o):s===D.compound?this.compound(i,o,a,r,n,l,d("compound")):s===D.shops?this.shopRow(i,o,a,r,n,l,d("safehouse"),d("market")):s===D.garage?this.garage(i,o,a,r,n,l):s===D.gangTower&&this.gangTower(i,o,a,r,n,l)}fountain(e,t,s){let i=new y.Mesh(new y.TorusGeometry(s,.35,10,40),L.concrete());i.rotation.x=Math.PI/2,i.position.set(e,.45,t);let o=new y.Mesh(new y.CircleGeometry(s,40),new y.MeshStandardMaterial({color:1920875,roughness:.05,metalness:.3,envMapIntensity:1.5}));o.rotation.x=-Math.PI/2,o.position.set(e,.55,t);let a=new y.Mesh(new y.CylinderGeometry(.35,.6,2.4,12),L.concrete());a.position.set(e,1.2,t);for(let n of[i,o,a])n.castShadow=n.receiveShadow=!0,this.three.add(n);this.solid(e-s,0,t-s,e+s,.8,t+s,["prop"]),this.post(e,t,.6,2.4,["prop"]),this.fountains=[...this.fountains??[],[e,t]]}plaza(e,t){let s=new y.Mesh(new y.CylinderGeometry(3,3.4,1.2,8),L.concrete());s.position.set(e,.6,t);let i=new y.Mesh(new y.CylinderGeometry(.4,1.1,14,4),new y.MeshStandardMaterial({color:12035466,roughness:.7}));i.position.set(e,8.2,t),i.rotation.y=Math.PI/4;let o=new y.Mesh(new y.OctahedronGeometry(.9),(()=>{let n=new y.MeshStandardMaterial({color:16765286,emissive:16765286,emissiveIntensity:0,metalness:.8,roughness:.2});return n.userData.night=3,this.sky.lamps.push(n),n})());o.position.set(e,15.8,t);for(let n of[s,i,o])n.castShadow=n.receiveShadow=!0,this.three.add(n);this.solid(e-3,0,t-3,e+3,1.2,t+3,["prop","cover"]),this.post(e,t,1.1,15,["prop"]);let a=[14034984,2792847,16032353,4553629,15320170,6966419];for(let n=0;n<6;n++){let r=n/6*Math.PI*2+.3;this.kiosk(e+Math.cos(r)*19,t+Math.sin(r)*19,-r+Math.PI/2,a[n])}for(let n=0;n<8;n++){let r=n/8*Math.PI*2;this.barrier(e+Math.cos(r)*11,t+Math.sin(r)*11,Math.abs(Math.cos(r))<.5)}for(let[n,r]of[[-20,-20],[20,-20],[-20,20],[20,20]])this.b.box(e+n-1.6,0,t+r-1.6,e+n+1.6,.7,t+r+1.6,"concrete"),this.solid(e+n-1.6,0,t+r-1.6,e+n+1.6,.7,t+r+1.6,["prop","cover"]),this.tree(e+n,t+r,1.1);this.spots.plaza=[e,t]}compound(e,t,s,i,o,a,n){this.b.box(s,0,a-.4,o,2.8,a,"dark"),this.solid(s,0,a-.4,o,2.8,a),this.b.box(s,0,i,s+.4,2.8,a,"dark"),this.solid(s,0,i,s+.4,2.8,a),this.b.box(o-.4,0,i,o,2.8,a,"dark"),this.solid(o-.4,0,i,o,2.8,a),this.b.box(s,0,i,e-9/2,2.8,i+.4,"dark"),this.solid(s,0,i,e-9/2,2.8,i+.4),this.b.box(e+9/2,0,i,o,2.8,i+.4,"dark"),this.solid(e+9/2,0,i,o,2.8,i+.4);let u=e-15,d=e+15,f=t+2,p=t+22,g=this.room(u,f,d,p,8.5,[{side:"s",at:-6,width:7},{side:"e",at:4,width:2.2}],{facade:"brickFacade",inside:"concrete",floor:"concrete",name:"warehouse"});for(let x of[-9,-3,3,9])this.b.box(e+x-.6,0,t+9,e+x+.6,3.2,t+17,{px:"metal",nx:"metal",py:"wood",pz:"metal",nz:"metal"}),this.solid(e+x-.6,0,t+9,e+x+.6,3.2,t+17,["wall","cover"]);this.crate(e-11,t+5,1.2,2),this.crate(e+12,t+6,1.2,1),this.crate(e+1,t+5.5,1.4,1);let v=[[.75,.2,.15],[.15,.35,.65],[.2,.5,.3],[.8,.55,.1]];this.container(e-18,t-10,!1,v[0]),this.container(e-13,t-14,!0,v[1]),this.container(e+16,t-8,!1,v[2]),this.container(e+10,t-15,!0,v[3]),this.crate(e-6,t-8),this.crate(e+5,t-6,1.2,2),this.crate(e-2,t-15),this.barrier(e,t-19,!0);let m={center:[e,t],gate:[e,i-2],inside:[e,t+12],yard:[e,t-9],room:g};n&&(this.spots.compound=m),(this.spots.compounds??=[]).push(m),this.pickupSpots.push([e+13,t+20],[e-13,t+4])}shopRow(e,t,s,i,o,a,n,r){let l=we/4;for(let h=0;h<4;h++){let u=s+h*l+.6,d=s+(h+1)*l-.6,f=n&&h===3,p=[{side:"s",at:0,width:f?2.2:3}];f&&p.push({side:"e",at:-1,width:4.2});let g=this.room(u,i,d,i+14,5.2,p,{name:f?"safehouse":r?"market":"shop"}),v=(u+d)/2;f?(this.b.box(u+.4,0,i+11.4,u+3.6,2.4,i+13.6,{px:"darkMetal",nx:"darkMetal",py:"darkMetal",pz:"darkMetal",nz:"darkMetal"}),this.solid(u+.4,0,i+11.4,u+3.6,2.4,i+13.6,["wall","locker"]),this.b.box(d-3.5,0,i+10.5,d-.6,.8,i+12.2,"wood"),this.solid(d-3.5,0,i+10.5,d-.6,.8,i+12.2,["prop","cover"]),this.spots.safehouse={door:[v,i-2],inside:[v,i+6],locker:[u+2,i+10.4],garage:[d+4,i+6],room:g}):(this.b.box(u+1,0,i+6,d-1,1.05,i+7,{px:"wood",nx:"wood",py:"wood",pz:"wood",nz:"wood"}),this.solid(u+1,0,i+6,d-1,1.05,i+7,["prop","cover"]),this.b.box(u+.4,0,i+12.6,d-.4,2.6,i+13.6,{pz:"wood",nz:"wood",py:"wood",px:"wood",nx:"wood"}),this.solid(u+.4,0,i+12.6,d-.4,2.6,i+13.6),this.pickupSpots.push([v,i+10])),r&&(this.spots.market??=[]).push([v,i-2])}this.b.box(s,3.3,i-2.2,o,3.45,i,{py:"darkMetal",ny:"darkMetal",nz:"darkMetal"}),this.tower(s+2,i+18,e-2,a-2,14+this.r.next()*14,this.r.pick(["concrete","plaster"])),this.tower(e+2,i+18,o-2,a-2,10+this.r.next()*18,this.r.pick(["brick","plaster"])),this.parking.push([s+4,i-5.4,0],[o-12,i-5.4,0])}garage(e,t,s,i,o,a){let r=s+2,l=o-2,h=i+2,u=a-2,d=7,f=l-d,p=h+4,g=u-2;this.b.box(r,8-.5,h,f,8,u,{py:"concrete",ny:"dark",px:"concrete",nx:"concrete",pz:"concrete",nz:"concrete"},4),this.b.box(f,8-.5,h,l,8,p,{py:"concrete",ny:"dark",px:"concrete",nz:"concrete",pz:"concrete"},4),this.b.box(f,8-.5,g,l,8,u,{py:"concrete",ny:"dark",px:"concrete",nz:"concrete",pz:"concrete"},4),this.surfaces.push({x0:r,x1:f,z0:h,z1:u,y:8},{x0:f,x1:l,z0:h,z1:p,y:8},{x0:f,x1:l,z0:g,z1:u,y:8}),this.slab(r,8-.5,h,f,8,u),this.slab(f,8-.5,h,l,8,p),this.slab(f,8-.5,g,l,8,u),this.b.slope("concrete",[f,8,p],[l,8,p],[l,.02,g],[f,.02,g],4),this.surfaces.push({x0:f,x1:l,z0:p,z1:g,ramp:"z",y0:8,y1:0}),this.b.box(f-.3,0,g-14,f,2.2,g,"concrete"),this.solid(f-.3,0,g-14,f,2.2,g,["wall"]);for(let x=r+1;x<f-1;x+=8)for(let b=h+1;b<u;b+=9)this.b.box(x-.35,0,b-.35,x+.35,8-.5,b+.35,"concrete"),this.solid(x-.35,0,b-.35,x+.35,8-.5,b+.35,["wall","cover"]);let v=1.1,m=(x,b,w,M)=>{this.b.box(x,8,b,w,8+v,M,"concrete"),this.solid(x,8,b,w,8+v,M,["wall","cover"])};m(r,h,l,h+.3),m(r,u-.3,l,u),m(r,h,r+.3,u),m(l-.3,h,l,p),m(f-.3,p,f,g);for(let[x,b]of[[r+4,h+4],[r+4,u-4],[f-6,u-4]])this.b.box(x-.08,8,b-.08,x+.08,12,b+.08,"darkMetal"),this.b.box(x-.4,12,b-.2,x+.4,8+4.15,b+.2,{ny:"lamp",py:"darkMetal"}),this.lampLights.push([x,8+3.8,b]);this.spots.garage={roof:[e-8,t],rampFoot:[l-d/2,u+4],center:[e,t]},this.parking.push([r+5,h+6,Math.PI/2],[r+13,h+6,Math.PI/2],[r+5,u-6,Math.PI/2])}gangTower(e,t,s,i,o,a){let n=e-15,r=e+15,l=t-12,h=t+14,u=6.5,d=this.room(n,l,r,h,u,[{side:"s",at:0,width:4},{side:"n",at:8,width:2.4}],{facade:"lobbyFacade",inside:"concrete",floor:"tile",glassSide:"s",name:"lobby"});this.tower(n,l,r,h,70,"glass",u+.4);for(let[f,p]of[[-8,-4],[8,-4],[-8,6],[8,6]])this.b.box(e+f-.6,0,t+p-.6,e+f+.6,u,t+p+.6,{px:"lobbyFacade",nx:"lobbyFacade",pz:"lobbyFacade",nz:"lobbyFacade"}),this.solid(e+f-.6,0,t+p-.6,e+f+.6,u,t+p+.6,["wall","cover"]);this.b.box(e-4,0,t+9,e+4,1.1,t+10.5,{px:"darkMetal",nx:"darkMetal",py:"wood",pz:"darkMetal",nz:"darkMetal"}),this.solid(e-4,0,t+9,e+4,1.1,t+10.5,["prop","cover"]);for(let f of[-10,-4,4,10])this.barrier(e+f,i+3,!0);this.spots.gangTower={door:[e,l-3],inside:[e,t],court:[e,i+6],room:d},this.parking.push([s+3,i+1.5,0],[o-7,i+1.5,0])}streets(){for(let e=0;e<=N;e++){let t=U(e);for(let s=0;s<N;s++){let i=U(s)+Je+4,o=U(s+1)-Je-4;for(let a=i;a<=o;a+=22)Math.abs(t)<X+1&&(this.lamp(t-8.2,a,0),this.lamp(t+8.2,a+11,Math.PI),this.lamp(a+11,t-8.2,-Math.PI/2),this.lamp(a,t+8.2,Math.PI/2));for(let a=i+6;a<=o-4;a+=33)this.tree(t-9.6,a,.85),this.tree(t+9.6,a+8,.85),this.tree(a+8,t-9.6,.85),this.tree(a,t+9.6,.85)}}for(let[e,t]of[[2,3],[4,5],[5,2],[3,6],[6,4]]){let s=U(e)+9,i=yt(t);this.b.box(s-.6,0,i-3,s+.6,.1,i+3,"metal"),this.b.box(s+.4,0,i-3,s+.5,2.6,i+3,"glass"),this.b.box(s-.9,2.6,i-3.2,s+.7,2.75,i+3.2,{py:"darkMetal",ny:"darkMetal",px:"darkMetal",nx:"darkMetal",pz:"darkMetal",nz:"darkMetal"}),this.bench(s+.1,i,-Math.PI/2),(this.spots.stages??=[]).push([s-2,i])}}edges(){let e=X+30,t=2;for(let[s,i,o,a]of[[-e,-e-t,e,-e],[-e,e,e,e+t],[-e-t,-e,-e,e],[e,-e,e+t,e]])this.b.box(s,0,i,o,3,a,"dark"),this.solid(s,0,i,o,6,a,["wall","edge"])}mountains(){let e=new y.BufferGeometry,t=[],s=1500,i=1900,o=90;for(let n=0;n<o;n++){let r=n/o*Math.PI*2,l=(n+1)/o*Math.PI*2,h=120+220*Math.pow(pe(n,7),2)+80*Math.sin(n*.7),u=120+220*Math.pow(pe(n+1,7),2)+80*Math.sin((n+1)*.7),d=(f,p,g)=>[Math.cos(f)*p,g,Math.sin(f)*p];t.push(...d(r,s,-5),...d(l,s,-5),...d(l,i,u),...d(r,s,-5),...d(l,i,u),...d(r,i,h))}e.setAttribute("position",new y.Float32BufferAttribute(t,3)),e.computeVertexNormals();let a=new y.Mesh(e,new y.MeshBasicMaterial({color:5267568,fog:!1,side:y.DoubleSide}));this.hills=a,this.three.add(a)}buildNav(){let t=Math.ceil((X*2+60)/2);this.nav={C:2,S:t,off:X+30,grid:new Uint8Array(t*t)};let s=this.nav.grid;for(let i of this.colliders){if(i.y0>1.5)continue;let o=Math.floor((i.x0+this.nav.off-.45)/2),a=Math.floor((i.x1+this.nav.off+.45)/2),n=Math.floor((i.z0+this.nav.off-.45)/2),r=Math.floor((i.z1+this.nav.off+.45)/2);for(let l=Math.max(0,o);l<=Math.min(t-1,a);l++)for(let h=Math.max(0,n);h<=Math.min(t-1,r);h++)s[h*t+l]=1}for(let[i,o]of this.doors??[]){let a=Math.floor((i+this.nav.off)/2),n=Math.floor((o+this.nav.off)/2);for(let r=-1;r<=1;r++)for(let l=-1;l<=1;l++){let h=(n+l)*t+(a+r);h>=0&&h<s.length&&Math.abs(r)+Math.abs(l)<2&&(s[h]=0)}}}navCell(e,t){return[Math.floor((e+this.nav.off)/this.nav.C),Math.floor((t+this.nav.off)/this.nav.C)]}navFree(e,t){let{S:s,grid:i}=this.nav;return e>=0&&t>=0&&e<s&&t<s&&i[t*s+e]===0}cellCentre(e,t){let{C:s,off:i}=this.nav;return[e*s-i+s/2,t*s-i+s/2]}path(e,t,s=6e3){let{S:i}=this.nav,[o,a]=this.navCell(e[0],e[1]),[n,r]=this.navCell(t[0],t[1]),l=(M,E)=>{for(let S=0;S<4;S++)for(let T=-S;T<=S;T++)for(let R=-S;R<=S;R++)if(this.navFree(M+T,E+R))return[M+T,E+R];return null},h=l(o,a),u=l(n,r);if(!h||!u)return null;[o,a]=h,[n,r]=u;let d=a*i+o,f=r*i+n,p=new Map([[d,0]]),g=new Map,v=[[0,d]],m=(M,E)=>{v.push([M,E]);let S=v.length-1;for(;S>0;){let T=S-1>>1;if(v[T][0]<=v[S][0])break;[v[T],v[S]]=[v[S],v[T]],S=T}},x=()=>{let M=v[0],E=v.pop();if(v.length){v[0]=E;let S=0;for(;;){let T=2*S+1,R=T+1,z=S;if(T<v.length&&v[T][0]<v[z][0]&&(z=T),R<v.length&&v[R][0]<v[z][0]&&(z=R),z===S)break;[v[z],v[S]]=[v[S],v[z]],S=z}}return M},b=new Set,w=0;for(;v.length&&w++<s;){let[,M]=x();if(M===f){let T=[],R=M;for(;R!==void 0;)T.unshift(this.cellCentre(R%i,Math.floor(R/i))),R=g.get(R);return this.smooth(T)}if(b.has(M))continue;b.add(M);let E=M%i,S=Math.floor(M/i);for(let[T,R,z]of[[1,0,1],[-1,0,1],[0,1,1],[0,-1,1],[1,1,1.414],[1,-1,1.414],[-1,1,1.414],[-1,-1,1.414]]){let k=E+T,P=S+R;if(!this.navFree(k,P)||T&&R&&(!this.navFree(E+T,S)||!this.navFree(E,S+R)))continue;let _=P*i+k,V=p.get(M)+z;V<(p.get(_)??1/0)&&(p.set(_,V),g.set(_,M),m(V+Math.hypot(k-n,P-r),_))}}return null}clearLine(e,t){let s=Math.hypot(t[0]-e[0],t[1]-e[1]),i=Math.ceil(s/1);for(let o=0;o<=i;o++){let a=o/Math.max(1,i),[n,r]=this.navCell(e[0]+(t[0]-e[0])*a,e[1]+(t[1]-e[1])*a);if(!this.navFree(n,r))return!1}return!0}smooth(e){if(e.length<3)return e;let t=[e[0]],s=0;for(;s<e.length-1;){let i=s+1;for(let o=e.length-1;o>s+1;o--)if(this.clearLine(e[s],e[o])){i=o;break}t.push(e[i]),s=i}return t}indoors(e,t){return this.interiors.find(s=>e>s.x0&&e<s.x1&&t>s.z0&&t<s.z1)??null}sidewalkSpot(e,t,s=0){for(let i=0;i<30;i++){let o=e.pick(this.sidewalkLoops),a=e.int(0,3),n=(a+1)%4,r=e.rand(),l=[o[a][0]+(o[n][0]-o[a][0])*r,o[a][1]+(o[n][1]-o[a][1])*r];if(!t||Math.hypot(l[0]-t[0],l[1]-t[1])>s)return l}return[0,0]}};import*as A from"three";var Gi="varying vec3 vDir; void main(){ vDir = normalize((modelMatrix * vec4(position, 0.0)).xyz); vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_Position = p.xyww; }",Oi=`
uniform vec3 uSun, uZenith, uHorizon, uSunCol, uCloud; uniform float uNight, uTime; varying vec3 vDir;
float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vn(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1,0)), f.x), mix(h21(i + vec2(0,1)), h21(i + vec2(1,1)), f.x), f.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++){ v += a * vn(p); p = p * 2.07 + 1.3; a *= 0.5; } return v; }
void main(){
  vec3 d = normalize(vDir);
  float h = d.y;
  vec3 col = mix(uHorizon, uZenith, pow(clamp(h, 0.0, 1.0), 0.45));
  col = mix(col, uHorizon * 0.55, smoothstep(0.0, -0.25, h));             // below the horizon
  float sd = max(dot(d, normalize(uSun)), 0.0);
  col += uSunCol * (pow(sd, 18.0) * 0.35 + pow(sd, 400.0) * 1.2);         // glow
  col += uSunCol * smoothstep(0.99955, 0.9998, sd) * 12.0 * (1.0 - uNight); // disc
  if (h > 0.0) {                                                            // clouds
    vec2 p = d.xz / (h + 0.12) * 0.55 + vec2(uTime * 0.004, uTime * 0.0015);
    float c = fbm(p * 1.4);
    float cov = smoothstep(0.52, 0.78, c) * smoothstep(0.02, 0.22, h);
    float lit = 0.75 + 0.25 * fbm(p * 2.8 + 4.0) + pow(sd, 6.0) * 0.6;
    col = mix(col, uCloud * lit, cov * 0.85);
  }
  gl_FragColor = vec4(col, 1.0);
}`,Et=class extends A.Mesh{constructor(){super(new A.SphereGeometry(1,32,16),new A.ShaderMaterial({vertexShader:Gi,fragmentShader:Oi,side:A.BackSide,depthWrite:!1,depthTest:!0,uniforms:{uSun:{value:new A.Vector3(0,1,0)},uZenith:{value:new A.Color},uHorizon:{value:new A.Color},uSunCol:{value:new A.Color},uCloud:{value:new A.Color},uNight:{value:0},uTime:{value:0}}})),this.frustumCulled=!1,this.renderOrder=-10}},Vi=16,St=class{constructor(e,t){this.scene=e,this.renderer=t,this.hour=16.5,this.sky=new Et,this.sky.scale.setScalar(4e3),e.add(this.sky),this.sun=new A.DirectionalLight(16777215,3),this.sun.castShadow=!0,this.sun.shadow.bias=-4e-4,this.sun.shadow.normalBias=.04;let s=this.sun.shadow.camera;s.near=1,s.far=400,this.setShadowSize(70),e.add(this.sun,this.sun.target),this.hemi=new A.HemisphereLight(12572927,3812898,.6),e.add(this.hemi);let i=2200,o=new Float32Array(i*3);for(let n=0;n<i;n++){let r=Math.random(),l=Math.random()*Math.PI*2,h=.05+r*.95,u=Math.sqrt(1-h*h);o.set([Math.cos(l)*u*3e3,h*3e3,Math.sin(l)*u*3e3],n*3)}let a=new A.BufferGeometry;a.setAttribute("position",new A.BufferAttribute(o,3)),this.stars=new A.Points(a,new A.PointsMaterial({color:16777215,size:1.6,sizeAttenuation:!1,transparent:!0,opacity:0,fog:!1,depthWrite:!1})),e.add(this.stars),this.moon=new A.Mesh(new A.SphereGeometry(60,24,16),new A.MeshBasicMaterial({color:14542591,fog:!1,transparent:!0,opacity:0})),e.add(this.moon),e.fog=new A.Fog(11057352,60,420),this.pmrem=new A.PMREMGenerator(t),this.envScene=new A.Scene,this.envSky=new Et,this.envSky.scale.setScalar(1e3),this.envSky.material=this.sky.material,this.envScene.add(this.envSky),this.envTarget=null,this.envHour=-99,this.sunDir=new A.Vector3,this.lamps=[],this.update(0,new A.Vector3)}setShadowSize(e){let t=this.sun.shadow.camera;t.left=t.bottom=-e,t.right=t.top=e,t.updateProjectionMatrix()}get night(){return st.uNight.value}get timeText(){let e=Math.floor(this.hour),t=Math.floor((this.hour-e)*60);return`${String(e).padStart(2,"0")}:${String(t).padStart(2,"0")}`}update(e,t){this.hour=(this.hour+e/60*(24/Vi))%24;let s=(this.hour-6)/12*Math.PI,i=Math.sin(s),o=i>-.05;this.sunDir.set(Math.cos(s)*.9,i,.35).normalize();let a=A.MathUtils.smoothstep(-i,-.02,.22);st.uNight.value=a;let n=1-A.MathUtils.smoothstep(i,.02,.4),r=this.sky.material.uniforms;r.uSun.value.copy(this.sunDir),r.uNight.value=a,r.uTime.value+=e;let l=(v,m,x)=>new A.Color(...v).lerp(new A.Color(...m),x),h=l([.16,.38,.95],[.22,.25,.6],n).lerp(new A.Color(.004,.008,.025),a),u=l([.62,.78,1],[1.15,.6,.35],n).lerp(new A.Color(.03,.04,.08),a);r.uZenith.value.copy(h).multiplyScalar(1.5),r.uHorizon.value.copy(u).multiplyScalar(1.5),r.uSunCol.value.copy(l([1,.95,.85],[1,.5,.2],n)).multiplyScalar(1-a*.9),r.uCloud.value.copy(l([1.25,1.25,1.3],[1.3,.75,.55],n)).lerp(new A.Color(.05,.06,.09),a);let d=o?this.sunDir:new A.Vector3(-this.sunDir.x,-this.sunDir.y,.2).normalize();this.sun.position.copy(t).addScaledVector(d,200),this.sun.target.position.copy(t);let f=A.MathUtils.smoothstep(i,0,.35),p=new A.Color(1,.55+.4*f,.32+.6*f);o?(this.sun.color.copy(p),this.sun.intensity=.2+3.2*A.MathUtils.smoothstep(i,-.05,.25)):(this.sun.color.setRGB(.55,.68,1),this.sun.intensity=.9),this.hemi.color.setRGB(.55+.2*f,.65+.15*f,.85).lerp(new A.Color(.3,.38,.6),a),this.hemi.groundColor.setRGB(.25,.2,.15).lerp(new A.Color(.12,.1,.1),a),this.hemi.intensity=.55+.45*(1-a)*(.6+.4*f);let g=u.clone().multiplyScalar(.62);this.scene.fog.color.copy(g),this.renderer.toneMappingExposure=.42+.12*(1-a)+a*.5,this.stars.material.opacity=a*.9,this.stars.position.copy(t),this.moon.material.opacity=a,this.moon.position.copy(t).addScaledVector(new A.Vector3(-this.sunDir.x,Math.abs(this.sunDir.y)+.25,-.4).normalize(),2600),this.sky.position.copy(t),this.hills&&this.hills.material.color.copy(u).multiplyScalar(.42+.1*a).lerp(new A.Color(.12,.16,.12),.25*(1-a));for(let v of this.lamps)v.emissiveIntensity=a*(v.userData.night??4);(Math.abs(this.hour-this.envHour)>.35||this.envTarget===null)&&(this.envHour=this.hour,this.envTarget?.dispose(),this.envTarget=this.pmrem.fromScene(this.envScene,0,.1,1100),this.scene.environment=this.envTarget.texture,this.scene.environmentIntensity=.15+.45*(1-a))}};import*as Se from"three";import{HalfFloatType as Xi,NoBlending as Qi,Timer as Zi,Vector2 as ii,WebGLRenderTarget as Ji}from"three";var he={name:"CopyShader",uniforms:{tDiffuse:{value:null},opacity:{value:1}},vertexShader:`

		varying vec2 vUv;

		void main() {

			vUv = uv;
			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

		}`,fragmentShader:`

		uniform float opacity;

		uniform sampler2D tDiffuse;

		varying vec2 vUv;

		void main() {

			vec4 texel = texture2D( tDiffuse, vUv );
			gl_FragColor = opacity * texel;


		}`};import{ShaderMaterial as si,UniformsUtils as $i}from"three";import{BufferGeometry as ji,Float32BufferAttribute as ti,OrthographicCamera as Wi,Mesh as qi}from"three";var Z=class{constructor(){this.isPass=!0,this.enabled=!0,this.needsSwap=!0,this.clear=!1,this.renderToScreen=!1}setSize(){}render(){console.error("THREE.Pass: .render() must be implemented in derived pass.")}dispose(){}},Yi=new Wi(-1,1,1,-1,0,1),Ts=class extends ji{constructor(){super(),this.setAttribute("position",new ti([-1,3,0,-1,-1,0,3,-1,0],3)),this.setAttribute("uv",new ti([0,2,0,0,2,0],2))}},Ki=new Ts,ce=class{constructor(e){this._mesh=new qi(Ki,e)}dispose(){this._mesh.geometry.dispose()}render(e){e.render(this._mesh,Yi)}get material(){return this._mesh.material}set material(e){this._mesh.material=e}};var ye=class extends Z{constructor(e,t="tDiffuse"){super(),this.textureID=t,this.uniforms=null,this.material=null,e instanceof si?(this.uniforms=e.uniforms,this.material=e):e&&(this.uniforms=$i.clone(e.uniforms),this.material=new si({name:e.name!==void 0?e.name:"unspecified",defines:Object.assign({},e.defines),uniforms:this.uniforms,vertexShader:e.vertexShader,fragmentShader:e.fragmentShader})),this._fsQuad=new ce(this.material)}render(e,t,s){this.uniforms[this.textureID]&&(this.uniforms[this.textureID].value=s.texture),this._fsQuad.material=this.material,this.renderToScreen?(e.setRenderTarget(null),this._fsQuad.render(e)):(e.setRenderTarget(t),this.clear&&e.clear(e.autoClearColor,e.autoClearDepth,e.autoClearStencil),this._fsQuad.render(e))}dispose(){this.material.dispose(),this._fsQuad.dispose()}};var ot=class extends Z{constructor(e,t){super(),this.scene=e,this.camera=t,this.clear=!0,this.needsSwap=!1,this.inverse=!1}render(e,t,s){let i=e.getContext(),o=e.state;o.buffers.color.setMask(!1),o.buffers.depth.setMask(!1),o.buffers.color.setLocked(!0),o.buffers.depth.setLocked(!0);let a,n;this.inverse?(a=0,n=1):(a=1,n=0),o.buffers.stencil.setTest(!0),o.buffers.stencil.setOp(i.REPLACE,i.REPLACE,i.REPLACE),o.buffers.stencil.setFunc(i.ALWAYS,a,4294967295),o.buffers.stencil.setClear(n),o.buffers.stencil.setLocked(!0),e.setRenderTarget(s),this.clear&&e.clear(),e.render(this.scene,this.camera),e.setRenderTarget(t),this.clear&&e.clear(),e.render(this.scene,this.camera),o.buffers.color.setLocked(!1),o.buffers.depth.setLocked(!1),o.buffers.color.setMask(!0),o.buffers.depth.setMask(!0),o.buffers.stencil.setLocked(!1),o.buffers.stencil.setFunc(i.EQUAL,1,4294967295),o.buffers.stencil.setOp(i.KEEP,i.KEEP,i.KEEP),o.buffers.stencil.setLocked(!0)}},Rt=class extends Z{constructor(){super(),this.needsSwap=!1}render(e){e.state.buffers.stencil.setLocked(!1),e.state.buffers.stencil.setTest(!1)}};var kt=class{constructor(e,t){if(this.renderer=e,this._pixelRatio=e.getPixelRatio(),t===void 0){let s=e.getSize(new ii);this._width=s.width,this._height=s.height,t=new Ji(this._width*this._pixelRatio,this._height*this._pixelRatio,{type:Xi}),t.texture.name="EffectComposer.rt1"}else this._width=t.width,this._height=t.height;this.renderTarget1=t,this.renderTarget2=t.clone(),this.renderTarget2.texture.name="EffectComposer.rt2",this.writeBuffer=this.renderTarget1,this.readBuffer=this.renderTarget2,this.renderToScreen=!0,this.passes=[],this.copyPass=new ye(he),this.copyPass.material.blending=Qi,this.timer=new Zi}swapBuffers(){let e=this.readBuffer;this.readBuffer=this.writeBuffer,this.writeBuffer=e}addPass(e){this.passes.push(e),e.setSize(this._width*this._pixelRatio,this._height*this._pixelRatio)}insertPass(e,t){this.passes.splice(t,0,e),e.setSize(this._width*this._pixelRatio,this._height*this._pixelRatio)}removePass(e){let t=this.passes.indexOf(e);t!==-1&&this.passes.splice(t,1)}isLastEnabledPass(e){for(let t=e+1;t<this.passes.length;t++)if(this.passes[t].enabled)return!1;return!0}render(e){this.timer.update(),e===void 0&&(e=this.timer.getDelta());let t=this.renderer.getRenderTarget(),s=!1;for(let i=0,o=this.passes.length;i<o;i++){let a=this.passes[i];if(a.enabled!==!1){if(a.renderToScreen=this.renderToScreen&&this.isLastEnabledPass(i),a.render(this.renderer,this.writeBuffer,this.readBuffer,e,s),a.needsSwap){if(s){let n=this.renderer.getContext(),r=this.renderer.state.buffers.stencil;r.setFunc(n.NOTEQUAL,1,4294967295),this.copyPass.render(this.renderer,this.writeBuffer,this.readBuffer,e),r.setFunc(n.EQUAL,1,4294967295)}this.swapBuffers()}ot!==void 0&&(a instanceof ot?s=!0:a instanceof Rt&&(s=!1))}}this.renderer.setRenderTarget(t)}reset(e){if(e===void 0){let t=this.renderer.getSize(new ii);this._pixelRatio=this.renderer.getPixelRatio(),this._width=t.width,this._height=t.height,e=this.renderTarget1.clone(),e.setSize(this._width*this._pixelRatio,this._height*this._pixelRatio)}this.renderTarget1.dispose(),this.renderTarget2.dispose(),this.renderTarget1=e,this.renderTarget2=e.clone(),this.writeBuffer=this.renderTarget1,this.readBuffer=this.renderTarget2}setSize(e,t){this._width=e,this._height=t;let s=this._width*this._pixelRatio,i=this._height*this._pixelRatio;this.renderTarget1.setSize(s,i),this.renderTarget2.setSize(s,i);for(let o=0;o<this.passes.length;o++)this.passes[o].setSize(s,i)}setPixelRatio(e){this._pixelRatio=e,this.setSize(this._width,this._height)}dispose(){this.renderTarget1.dispose(),this.renderTarget2.dispose(),this.copyPass.dispose()}};import{Color as eo}from"three";var zt=class extends Z{constructor(e,t,s=null,i=null,o=null){super(),this.scene=e,this.camera=t,this.overrideMaterial=s,this.clearColor=i,this.clearAlpha=o,this.clear=!0,this.clearDepth=!1,this.needsSwap=!1,this.isRenderPass=!0,this._oldClearColor=new eo}render(e,t,s){let i=e.autoClear;e.autoClear=!1;let o,a;this.overrideMaterial!==null&&(a=this.scene.overrideMaterial,this.scene.overrideMaterial=this.overrideMaterial),this.clearColor!==null&&(e.getClearColor(this._oldClearColor),e.setClearColor(this.clearColor,e.getClearAlpha())),this.clearAlpha!==null&&(o=e.getClearAlpha(),e.setClearAlpha(this.clearAlpha)),this.clearDepth==!0&&e.clearDepth(),e.setRenderTarget(this.renderToScreen?null:s),this.clear===!0&&e.clear(e.autoClearColor,e.autoClearDepth,e.autoClearStencil),e.render(this.scene,this.camera),this.clearColor!==null&&e.setClearColor(this._oldClearColor),this.clearAlpha!==null&&e.setClearAlpha(o),this.overrideMaterial!==null&&(this.scene.overrideMaterial=a),e.autoClear=i}};import{AdditiveBlending as so,Color as ai,HalfFloatType as Es,MeshBasicMaterial as io,ShaderMaterial as At,UniformsUtils as ni,Vector2 as Te,Vector3 as at,WebGLRenderTarget as Ss}from"three";import{Color as to}from"three";var oi={name:"LuminosityHighPassShader",uniforms:{tDiffuse:{value:null},luminosityThreshold:{value:1},smoothWidth:{value:1},defaultColor:{value:new to(0)},defaultOpacity:{value:0}},vertexShader:`

		varying vec2 vUv;

		void main() {

			vUv = uv;

			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

		}`,fragmentShader:`

		uniform sampler2D tDiffuse;
		uniform vec3 defaultColor;
		uniform float defaultOpacity;
		uniform float luminosityThreshold;
		uniform float smoothWidth;

		varying vec2 vUv;

		void main() {

			vec4 texel = texture2D( tDiffuse, vUv );

			float v = luminance( texel.xyz );

			vec4 outputColor = vec4( defaultColor.rgb, defaultOpacity );

			float alpha = smoothstep( luminosityThreshold, luminosityThreshold + smoothWidth, v );

			gl_FragColor = mix( outputColor, texel, alpha );

		}`};var Ie=class c extends Z{constructor(e,t=1,s,i){super(),this.strength=t,this.radius=s,this.threshold=i,this.resolution=e!==void 0?new Te(e.x,e.y):new Te(256,256),this.clearColor=new ai(0,0,0),this.needsSwap=!1,this.renderTargetsHorizontal=[],this.renderTargetsVertical=[],this.nMips=5;let o=Math.round(this.resolution.x/2),a=Math.round(this.resolution.y/2);this.renderTargetBright=new Ss(o,a,{type:Es,depthBuffer:!1}),this.renderTargetBright.texture.name="UnrealBloomPass.bright",this.renderTargetBright.texture.generateMipmaps=!1;for(let h=0;h<this.nMips;h++){let u=new Ss(o,a,{type:Es,depthBuffer:!1});u.texture.name="UnrealBloomPass.h"+h,u.texture.generateMipmaps=!1,this.renderTargetsHorizontal.push(u);let d=new Ss(o,a,{type:Es,depthBuffer:!1});d.texture.name="UnrealBloomPass.v"+h,d.texture.generateMipmaps=!1,this.renderTargetsVertical.push(d),o=Math.round(o/2),a=Math.round(a/2)}let n=oi;this.highPassUniforms=ni.clone(n.uniforms),this.highPassUniforms.luminosityThreshold.value=i,this.highPassUniforms.smoothWidth.value=.01,this.materialHighPassFilter=new At({uniforms:this.highPassUniforms,vertexShader:n.vertexShader,fragmentShader:n.fragmentShader}),this.separableBlurMaterials=[];let r=[6,10,14,18,22];o=Math.round(this.resolution.x/2),a=Math.round(this.resolution.y/2);for(let h=0;h<this.nMips;h++)this.separableBlurMaterials.push(this._getSeparableBlurMaterial(r[h])),this.separableBlurMaterials[h].uniforms.invSize.value=new Te(1/o,1/a),o=Math.round(o/2),a=Math.round(a/2);this.compositeMaterial=this._getCompositeMaterial(this.nMips),this.compositeMaterial.uniforms.blurTexture1.value=this.renderTargetsVertical[0].texture,this.compositeMaterial.uniforms.blurTexture2.value=this.renderTargetsVertical[1].texture,this.compositeMaterial.uniforms.blurTexture3.value=this.renderTargetsVertical[2].texture,this.compositeMaterial.uniforms.blurTexture4.value=this.renderTargetsVertical[3].texture,this.compositeMaterial.uniforms.blurTexture5.value=this.renderTargetsVertical[4].texture,this.compositeMaterial.uniforms.bloomStrength.value=t,this.compositeMaterial.uniforms.bloomRadius.value=.1;let l=[1,.8,.6,.4,.2];this.compositeMaterial.uniforms.bloomFactors.value=l,this.bloomTintColors=[new at(1,1,1),new at(1,1,1),new at(1,1,1),new at(1,1,1),new at(1,1,1)],this.compositeMaterial.uniforms.bloomTintColors.value=this.bloomTintColors,this.copyUniforms=ni.clone(he.uniforms),this.blendMaterial=new At({uniforms:this.copyUniforms,vertexShader:he.vertexShader,fragmentShader:he.fragmentShader,premultipliedAlpha:!0,blending:so,depthTest:!1,depthWrite:!1,transparent:!0}),this._oldClearColor=new ai,this._oldClearAlpha=1,this._basic=new io,this._fsQuad=new ce(null)}dispose(){for(let e=0;e<this.renderTargetsHorizontal.length;e++)this.renderTargetsHorizontal[e].dispose();for(let e=0;e<this.renderTargetsVertical.length;e++)this.renderTargetsVertical[e].dispose();this.renderTargetBright.dispose();for(let e=0;e<this.separableBlurMaterials.length;e++)this.separableBlurMaterials[e].dispose();this.compositeMaterial.dispose(),this.blendMaterial.dispose(),this._basic.dispose(),this._fsQuad.dispose()}setSize(e,t){let s=Math.round(e/2),i=Math.round(t/2);this.renderTargetBright.setSize(s,i);for(let o=0;o<this.nMips;o++)this.renderTargetsHorizontal[o].setSize(s,i),this.renderTargetsVertical[o].setSize(s,i),this.separableBlurMaterials[o].uniforms.invSize.value=new Te(1/s,1/i),s=Math.round(s/2),i=Math.round(i/2)}render(e,t,s,i,o){e.getClearColor(this._oldClearColor),this._oldClearAlpha=e.getClearAlpha();let a=e.autoClear;e.autoClear=!1,e.setClearColor(this.clearColor,0),o&&e.state.buffers.stencil.setTest(!1),this.renderToScreen&&(this._fsQuad.material=this._basic,this._basic.map=s.texture,e.setRenderTarget(null),e.clear(),this._fsQuad.render(e)),this.highPassUniforms.tDiffuse.value=s.texture,this.highPassUniforms.luminosityThreshold.value=this.threshold,this._fsQuad.material=this.materialHighPassFilter,e.setRenderTarget(this.renderTargetBright),e.clear(),this._fsQuad.render(e);let n=this.renderTargetBright;for(let r=0;r<this.nMips;r++)this._fsQuad.material=this.separableBlurMaterials[r],this.separableBlurMaterials[r].uniforms.colorTexture.value=n.texture,this.separableBlurMaterials[r].uniforms.direction.value=c.BlurDirectionX,e.setRenderTarget(this.renderTargetsHorizontal[r]),e.clear(),this._fsQuad.render(e),this.separableBlurMaterials[r].uniforms.colorTexture.value=this.renderTargetsHorizontal[r].texture,this.separableBlurMaterials[r].uniforms.direction.value=c.BlurDirectionY,e.setRenderTarget(this.renderTargetsVertical[r]),e.clear(),this._fsQuad.render(e),n=this.renderTargetsVertical[r];this._fsQuad.material=this.compositeMaterial,this.compositeMaterial.uniforms.bloomStrength.value=this.strength,this.compositeMaterial.uniforms.bloomRadius.value=this.radius,this.compositeMaterial.uniforms.bloomTintColors.value=this.bloomTintColors,e.setRenderTarget(this.renderTargetsHorizontal[0]),e.clear(),this._fsQuad.render(e),this._fsQuad.material=this.blendMaterial,this.copyUniforms.tDiffuse.value=this.renderTargetsHorizontal[0].texture,o&&e.state.buffers.stencil.setTest(!0),this.renderToScreen?(e.setRenderTarget(null),this._fsQuad.render(e)):(e.setRenderTarget(s),this._fsQuad.render(e)),e.setClearColor(this._oldClearColor,this._oldClearAlpha),e.autoClear=a}_getSeparableBlurMaterial(e){let t=[],s=e/3;for(let a=0;a<e;a++)t.push(.39894*Math.exp(-.5*a*a/(s*s))/s);let i=[],o=[];for(let a=1;a<e;a+=2){let n=t[a],r=a+1<e?t[a+1]:0,l=n+r;i.push((a*n+(a+1)*r)/l),o.push(l)}return new At({defines:{KERNEL_PAIRS:i.length},uniforms:{colorTexture:{value:null},invSize:{value:new Te(.5,.5)},direction:{value:new Te(.5,.5)},centerWeight:{value:t[0]},gaussianOffsets:{value:i},gaussianWeights:{value:o}},vertexShader:`

				varying vec2 vUv;

				void main() {

					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

				}`,fragmentShader:`

				#include <common>

				varying vec2 vUv;

				uniform sampler2D colorTexture;
				uniform vec2 invSize;
				uniform vec2 direction;
				uniform float centerWeight;
				uniform float gaussianOffsets[KERNEL_PAIRS];
				uniform float gaussianWeights[KERNEL_PAIRS];

				void main() {

					vec3 diffuseSum = texture2D( colorTexture, vUv ).rgb * centerWeight;

					for ( int i = 0; i < KERNEL_PAIRS; i ++ ) {

						vec2 uvOffset = direction * invSize * gaussianOffsets[ i ];
						vec3 sample1 = texture2D( colorTexture, vUv + uvOffset ).rgb;
						vec3 sample2 = texture2D( colorTexture, vUv - uvOffset ).rgb;
						diffuseSum += ( sample1 + sample2 ) * gaussianWeights[ i ];

					}

					gl_FragColor = vec4( diffuseSum, 1.0 );

				}`})}_getCompositeMaterial(e){return new At({defines:{NUM_MIPS:e},uniforms:{blurTexture1:{value:null},blurTexture2:{value:null},blurTexture3:{value:null},blurTexture4:{value:null},blurTexture5:{value:null},bloomStrength:{value:1},bloomFactors:{value:null},bloomTintColors:{value:null},bloomRadius:{value:0}},vertexShader:`

				varying vec2 vUv;

				void main() {

					vUv = uv;
					gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

				}`,fragmentShader:`

				varying vec2 vUv;

				uniform sampler2D blurTexture1;
				uniform sampler2D blurTexture2;
				uniform sampler2D blurTexture3;
				uniform sampler2D blurTexture4;
				uniform sampler2D blurTexture5;
				uniform float bloomStrength;
				uniform float bloomRadius;
				uniform float bloomFactors[NUM_MIPS];
				uniform vec3 bloomTintColors[NUM_MIPS];

				float lerpBloomFactor( const in float factor ) {

					float mirrorFactor = 1.2 - factor;
					return mix( factor, mirrorFactor, bloomRadius );

				}

				void main() {

					// 3.0 for backwards compatibility with previous alpha-based intensity
					vec3 bloom = 3.0 * bloomStrength * (
						lerpBloomFactor( bloomFactors[ 0 ] ) * bloomTintColors[ 0 ] * texture2D( blurTexture1, vUv ).rgb +
						lerpBloomFactor( bloomFactors[ 1 ] ) * bloomTintColors[ 1 ] * texture2D( blurTexture2, vUv ).rgb +
						lerpBloomFactor( bloomFactors[ 2 ] ) * bloomTintColors[ 2 ] * texture2D( blurTexture3, vUv ).rgb +
						lerpBloomFactor( bloomFactors[ 3 ] ) * bloomTintColors[ 3 ] * texture2D( blurTexture4, vUv ).rgb +
						lerpBloomFactor( bloomFactors[ 4 ] ) * bloomTintColors[ 4 ] * texture2D( blurTexture5, vUv ).rgb
					);

					float bloomAlpha = max( bloom.r, max( bloom.g, bloom.b ) );
					gl_FragColor = vec4( bloom, bloomAlpha );

				}`})}};Ie.BlurDirectionX=new Te(1,0);Ie.BlurDirectionY=new Te(0,1);import{ColorManagement as oo,RawShaderMaterial as ao,UniformsUtils as no,LinearToneMapping as ro,ReinhardToneMapping as lo,CineonToneMapping as ho,AgXToneMapping as co,ACESFilmicToneMapping as uo,NeutralToneMapping as po,CustomToneMapping as fo,SRGBTransfer as mo}from"three";var nt={name:"OutputShader",uniforms:{tDiffuse:{value:null},toneMappingExposure:{value:1}},vertexShader:`
		precision highp float;

		uniform mat4 modelViewMatrix;
		uniform mat4 projectionMatrix;

		attribute vec3 position;
		attribute vec2 uv;

		varying vec2 vUv;

		void main() {

			vUv = uv;
			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

		}`,fragmentShader:`

		precision highp float;

		uniform sampler2D tDiffuse;

		#include <tonemapping_pars_fragment>
		#include <colorspace_pars_fragment>

		varying vec2 vUv;

		void main() {

			gl_FragColor = texture2D( tDiffuse, vUv );

			// tone mapping

			#ifdef LINEAR_TONE_MAPPING

				gl_FragColor.rgb = LinearToneMapping( gl_FragColor.rgb );

			#elif defined( REINHARD_TONE_MAPPING )

				gl_FragColor.rgb = ReinhardToneMapping( gl_FragColor.rgb );

			#elif defined( CINEON_TONE_MAPPING )

				gl_FragColor.rgb = CineonToneMapping( gl_FragColor.rgb );

			#elif defined( ACES_FILMIC_TONE_MAPPING )

				gl_FragColor.rgb = ACESFilmicToneMapping( gl_FragColor.rgb );

			#elif defined( AGX_TONE_MAPPING )

				gl_FragColor.rgb = AgXToneMapping( gl_FragColor.rgb );

			#elif defined( NEUTRAL_TONE_MAPPING )

				gl_FragColor.rgb = NeutralToneMapping( gl_FragColor.rgb );

			#elif defined( CUSTOM_TONE_MAPPING )

				gl_FragColor.rgb = CustomToneMapping( gl_FragColor.rgb );

			#endif

			// color space

			#ifdef SRGB_TRANSFER

				gl_FragColor = sRGBTransferOETF( gl_FragColor );

			#endif

		}`};var Pt=class extends Z{constructor(){super(),this.isOutputPass=!0,this.uniforms=no.clone(nt.uniforms),this.material=new ao({name:nt.name,uniforms:this.uniforms,vertexShader:nt.vertexShader,fragmentShader:nt.fragmentShader}),this._fsQuad=new ce(this.material),this._outputColorSpace=null,this._toneMapping=null}render(e,t,s){this.uniforms.tDiffuse.value=s.texture,this.uniforms.toneMappingExposure.value=e.toneMappingExposure,(this._outputColorSpace!==e.outputColorSpace||this._toneMapping!==e.toneMapping)&&(this._outputColorSpace=e.outputColorSpace,this._toneMapping=e.toneMapping,this.material.defines={},oo.getTransfer(this._outputColorSpace)===mo&&(this.material.defines.SRGB_TRANSFER=""),this._toneMapping===ro?this.material.defines.LINEAR_TONE_MAPPING="":this._toneMapping===lo?this.material.defines.REINHARD_TONE_MAPPING="":this._toneMapping===ho?this.material.defines.CINEON_TONE_MAPPING="":this._toneMapping===uo?this.material.defines.ACES_FILMIC_TONE_MAPPING="":this._toneMapping===co?this.material.defines.AGX_TONE_MAPPING="":this._toneMapping===po?this.material.defines.NEUTRAL_TONE_MAPPING="":this._toneMapping===fo&&(this.material.defines.CUSTOM_TONE_MAPPING=""),this.material.needsUpdate=!0),this.renderToScreen===!0?(e.setRenderTarget(null),this._fsQuad.render(e)):(e.setRenderTarget(t),this.clear&&e.clear(e.autoClearColor,e.autoClearDepth,e.autoClearStencil),this._fsQuad.render(e))}dispose(){this.material.dispose(),this._fsQuad.dispose()}};import{Vector2 as go}from"three";var ri={name:"FXAAShader",uniforms:{tDiffuse:{value:null},resolution:{value:new go(1/1024,1/512)}},vertexShader:`

		varying vec2 vUv;

		void main() {

			vUv = uv;
			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );

		}`,fragmentShader:`

		uniform sampler2D tDiffuse;
		uniform vec2 resolution;
		varying vec2 vUv;

		#define EDGE_STEP_COUNT 6
		#define EDGE_GUESS 8.0
		#define EDGE_STEPS 1.0, 1.5, 2.0, 2.0, 2.0, 4.0
		const float edgeSteps[EDGE_STEP_COUNT] = float[EDGE_STEP_COUNT]( EDGE_STEPS );

		float _ContrastThreshold = 0.0312;
		float _RelativeThreshold = 0.063;
		float _SubpixelBlending = 1.0;

		vec4 Sample( sampler2D  tex2D, vec2 uv ) {

			return texture( tex2D, uv );

		}

		float SampleLuminance( sampler2D tex2D, vec2 uv ) {

			return dot( Sample( tex2D, uv ).rgb, vec3( 0.3, 0.59, 0.11 ) );

		}

		float SampleLuminance( sampler2D tex2D, vec2 texSize, vec2 uv, float uOffset, float vOffset ) {

			uv += texSize * vec2(uOffset, vOffset);
			return SampleLuminance(tex2D, uv);

		}

		struct LuminanceData {

			float m, n, e, s, w;
			float ne, nw, se, sw;
			float highest, lowest, contrast;

		};

		LuminanceData SampleLuminanceNeighborhood( sampler2D tex2D, vec2 texSize, vec2 uv ) {

			LuminanceData l;
			l.m = SampleLuminance( tex2D, uv );
			l.n = SampleLuminance( tex2D, texSize, uv,  0.0,  1.0 );
			l.e = SampleLuminance( tex2D, texSize, uv,  1.0,  0.0 );
			l.s = SampleLuminance( tex2D, texSize, uv,  0.0, -1.0 );
			l.w = SampleLuminance( tex2D, texSize, uv, -1.0,  0.0 );

			l.ne = SampleLuminance( tex2D, texSize, uv,  1.0,  1.0 );
			l.nw = SampleLuminance( tex2D, texSize, uv, -1.0,  1.0 );
			l.se = SampleLuminance( tex2D, texSize, uv,  1.0, -1.0 );
			l.sw = SampleLuminance( tex2D, texSize, uv, -1.0, -1.0 );

			l.highest = max( max( max( max( l.n, l.e ), l.s ), l.w ), l.m );
			l.lowest = min( min( min( min( l.n, l.e ), l.s ), l.w ), l.m );
			l.contrast = l.highest - l.lowest;
			return l;

		}

		bool ShouldSkipPixel( LuminanceData l ) {

			float threshold = max( _ContrastThreshold, _RelativeThreshold * l.highest );
			return l.contrast < threshold;

		}

		float DeterminePixelBlendFactor( LuminanceData l ) {

			float f = 2.0 * ( l.n + l.e + l.s + l.w );
			f += l.ne + l.nw + l.se + l.sw;
			f *= 1.0 / 12.0;
			f = abs( f - l.m );
			f = clamp( f / l.contrast, 0.0, 1.0 );

			float blendFactor = smoothstep( 0.0, 1.0, f );
			return blendFactor * blendFactor * _SubpixelBlending;

		}

		struct EdgeData {

			bool isHorizontal;
			float pixelStep;
			float oppositeLuminance, gradient;

		};

		EdgeData DetermineEdge( vec2 texSize, LuminanceData l ) {

			EdgeData e;
			float horizontal =
				abs( l.n + l.s - 2.0 * l.m ) * 2.0 +
				abs( l.ne + l.se - 2.0 * l.e ) +
				abs( l.nw + l.sw - 2.0 * l.w );
			float vertical =
				abs( l.e + l.w - 2.0 * l.m ) * 2.0 +
				abs( l.ne + l.nw - 2.0 * l.n ) +
				abs( l.se + l.sw - 2.0 * l.s );
			e.isHorizontal = horizontal >= vertical;

			float pLuminance = e.isHorizontal ? l.n : l.e;
			float nLuminance = e.isHorizontal ? l.s : l.w;
			float pGradient = abs( pLuminance - l.m );
			float nGradient = abs( nLuminance - l.m );

			e.pixelStep = e.isHorizontal ? texSize.y : texSize.x;

			if (pGradient < nGradient) {

				e.pixelStep = -e.pixelStep;
				e.oppositeLuminance = nLuminance;
				e.gradient = nGradient;

			} else {

				e.oppositeLuminance = pLuminance;
				e.gradient = pGradient;

			}

			return e;

		}

		float DetermineEdgeBlendFactor( sampler2D  tex2D, vec2 texSize, LuminanceData l, EdgeData e, vec2 uv ) {

			vec2 uvEdge = uv;
			vec2 edgeStep;
			if (e.isHorizontal) {

				uvEdge.y += e.pixelStep * 0.5;
				edgeStep = vec2( texSize.x, 0.0 );

			} else {

				uvEdge.x += e.pixelStep * 0.5;
				edgeStep = vec2( 0.0, texSize.y );

			}

			float edgeLuminance = ( l.m + e.oppositeLuminance ) * 0.5;
			float gradientThreshold = e.gradient * 0.25;

			vec2 puv = uvEdge + edgeStep * edgeSteps[0];
			float pLuminanceDelta = SampleLuminance( tex2D, puv ) - edgeLuminance;
			bool pAtEnd = abs( pLuminanceDelta ) >= gradientThreshold;

			for ( int i = 1; i < EDGE_STEP_COUNT && !pAtEnd; i++ ) {

				puv += edgeStep * edgeSteps[i];
				pLuminanceDelta = SampleLuminance( tex2D, puv ) - edgeLuminance;
				pAtEnd = abs( pLuminanceDelta ) >= gradientThreshold;

			}

			if ( !pAtEnd ) {

				puv += edgeStep * EDGE_GUESS;

			}

			vec2 nuv = uvEdge - edgeStep * edgeSteps[0];
			float nLuminanceDelta = SampleLuminance( tex2D, nuv ) - edgeLuminance;
			bool nAtEnd = abs( nLuminanceDelta ) >= gradientThreshold;

			for ( int i = 1; i < EDGE_STEP_COUNT && !nAtEnd; i++ ) {

				nuv -= edgeStep * edgeSteps[i];
				nLuminanceDelta = SampleLuminance( tex2D, nuv ) - edgeLuminance;
				nAtEnd = abs( nLuminanceDelta ) >= gradientThreshold;

			}

			if ( !nAtEnd ) {

				nuv -= edgeStep * EDGE_GUESS;

			}

			float pDistance, nDistance;
			if ( e.isHorizontal ) {

				pDistance = puv.x - uv.x;
				nDistance = uv.x - nuv.x;

			} else {

				pDistance = puv.y - uv.y;
				nDistance = uv.y - nuv.y;

			}

			float shortestDistance;
			bool deltaSign;
			if ( pDistance <= nDistance ) {

				shortestDistance = pDistance;
				deltaSign = pLuminanceDelta >= 0.0;

			} else {

				shortestDistance = nDistance;
				deltaSign = nLuminanceDelta >= 0.0;

			}

			if ( deltaSign == ( l.m - edgeLuminance >= 0.0 ) ) {

				return 0.0;

			}

			return 0.5 - shortestDistance / ( pDistance + nDistance );

		}

		vec4 ApplyFXAA( sampler2D  tex2D, vec2 texSize, vec2 uv ) {

			LuminanceData luminance = SampleLuminanceNeighborhood( tex2D, texSize, uv );
			if ( ShouldSkipPixel( luminance ) ) {

				return Sample( tex2D, uv );

			}

			float pixelBlend = DeterminePixelBlendFactor( luminance );
			EdgeData edge = DetermineEdge( texSize, luminance );
			float edgeBlend = DetermineEdgeBlendFactor( tex2D, texSize, luminance, edge, uv );
			float finalBlend = max( pixelBlend, edgeBlend );

			if (edge.isHorizontal) {

				uv.y += edge.pixelStep * finalBlend;

			} else {

				uv.x += edge.pixelStep * finalBlend;

			}

			return Sample( tex2D, uv );

		}

		void main() {

			gl_FragColor = ApplyFXAA( tDiffuse, resolution.xy, vUv );

		}`};var Ht=class extends ye{constructor(){super(ri)}setSize(e,t){this.material.uniforms.resolution.value.set(1/e,1/t)}};import{AddEquation as _t,Color as Eo,CustomBlending as So,DataTexture as Ro,DepthTexture as ko,DepthStencilFormat as zo,DstAlphaFactor as ci,DstColorFactor as ui,HalfFloatType as di,MeshNormalMaterial as Ao,NearestFilter as pi,NoBlending as Ee,RepeatWrapping as fi,RGBAFormat as Po,ShaderMaterial as ct,UniformsUtils as ut,UnsignedByteType as Ho,UnsignedInt248Type as Co,WebGLRenderTarget as mi,ZeroFactor as Lt}from"three";import{DataTexture as vo,Matrix4 as Rs,RepeatWrapping as li,Vector2 as xo,Vector3 as ks}from"three";var rt={name:"GTAOShader",defines:{PERSPECTIVE_CAMERA:1,SAMPLES:16,NORMAL_VECTOR_TYPE:1,SCREEN_SPACE_RADIUS:0,SCREEN_SPACE_RADIUS_SCALE:100,SCENE_CLIP_BOX:0},uniforms:{tNormal:{value:null},tDepth:{value:null},tNoise:{value:null},resolution:{value:new xo},cameraNear:{value:null},cameraFar:{value:null},cameraProjectionMatrix:{value:new Rs},cameraProjectionMatrixInverse:{value:new Rs},cameraWorldMatrix:{value:new Rs},radius:{value:.25},distanceExponent:{value:1},thickness:{value:1},distanceFallOff:{value:1},scale:{value:1},sceneBoxMin:{value:new ks(-1,-1,-1)},sceneBoxMax:{value:new ks(1,1,1)}},vertexShader:`

		varying vec2 vUv;

		void main() {
			vUv = uv;
			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
		}`,fragmentShader:`
		varying vec2 vUv;
		uniform highp sampler2D tNormal;
		uniform highp sampler2D tDepth;
		uniform sampler2D tNoise;
		uniform vec2 resolution;
		uniform float cameraNear;
		uniform float cameraFar;
		uniform mat4 cameraProjectionMatrix;
		uniform mat4 cameraProjectionMatrixInverse;
		uniform mat4 cameraWorldMatrix;
		uniform float radius;
		uniform float distanceExponent;
		uniform float thickness;
		uniform float distanceFallOff;
		uniform float scale;
		#if SCENE_CLIP_BOX == 1
			uniform vec3 sceneBoxMin;
			uniform vec3 sceneBoxMax;
		#endif

		#include <common>
		#include <packing>

		#ifndef FRAGMENT_OUTPUT
		#define FRAGMENT_OUTPUT vec4(vec3(ao), 1.)
		#endif

		vec3 getViewPosition( const in vec2 screenPosition, const in float depth ) {
			#ifdef USE_REVERSED_DEPTH_BUFFER
				vec4 clipSpacePosition = vec4( vec2( screenPosition ) * 2.0 - 1.0, depth, 1.0 );
			#else
				vec4 clipSpacePosition = vec4( vec3( screenPosition, depth ) * 2.0 - 1.0, 1.0 );
			#endif
			vec4 viewSpacePosition = cameraProjectionMatrixInverse * clipSpacePosition;
			return viewSpacePosition.xyz / viewSpacePosition.w;
		}

		float getDepth(const vec2 uv) {
			return textureLod(tDepth, uv.xy, 0.0).r;
		}

		float fetchDepth(const ivec2 uv) {
			return texelFetch(tDepth, uv.xy, 0).r;
		}

		float getViewZ(const in float depth) {
			#if PERSPECTIVE_CAMERA == 1
				return perspectiveDepthToViewZ(depth, cameraNear, cameraFar);
			#else
				return orthographicDepthToViewZ(depth, cameraNear, cameraFar);
			#endif
		}

		vec3 computeNormalFromDepth(const vec2 uv) {
			vec2 size = vec2(textureSize(tDepth, 0));
			ivec2 p = ivec2(uv * size);
			float c0 = fetchDepth(p);
			float l2 = fetchDepth(p - ivec2(2, 0));
			float l1 = fetchDepth(p - ivec2(1, 0));
			float r1 = fetchDepth(p + ivec2(1, 0));
			float r2 = fetchDepth(p + ivec2(2, 0));
			float b2 = fetchDepth(p - ivec2(0, 2));
			float b1 = fetchDepth(p - ivec2(0, 1));
			float t1 = fetchDepth(p + ivec2(0, 1));
			float t2 = fetchDepth(p + ivec2(0, 2));
			float dl = abs((2.0 * l1 - l2) - c0);
			float dr = abs((2.0 * r1 - r2) - c0);
			float db = abs((2.0 * b1 - b2) - c0);
			float dt = abs((2.0 * t1 - t2) - c0);
			vec3 ce = getViewPosition(uv, c0).xyz;
			vec3 dpdx = (dl < dr) ? ce - getViewPosition((uv - vec2(1.0 / size.x, 0.0)), l1).xyz : -ce + getViewPosition((uv + vec2(1.0 / size.x, 0.0)), r1).xyz;
			vec3 dpdy = (db < dt) ? ce - getViewPosition((uv - vec2(0.0, 1.0 / size.y)), b1).xyz : -ce + getViewPosition((uv + vec2(0.0, 1.0 / size.y)), t1).xyz;
			return normalize(cross(dpdx, dpdy));
		}

		vec3 getViewNormal(const vec2 uv) {
			#if NORMAL_VECTOR_TYPE == 2
				return normalize(textureLod(tNormal, uv, 0.).rgb);
			#elif NORMAL_VECTOR_TYPE == 1
				return unpackRGBToNormal(textureLod(tNormal, uv, 0.).rgb);
			#else
				return computeNormalFromDepth(uv);
			#endif
		}

		vec3 getSceneUvAndDepth(vec3 sampleViewPos) {
			vec4 sampleClipPos = cameraProjectionMatrix * vec4(sampleViewPos, 1.);
			vec2 sampleUv = sampleClipPos.xy / sampleClipPos.w * 0.5 + 0.5;
			float sampleSceneDepth = getDepth(sampleUv);
			return vec3(sampleUv, sampleSceneDepth);
		}

		void main() {
			float depth = getDepth(vUv.xy);

			#ifdef USE_REVERSED_DEPTH_BUFFER
				if (depth <= 0.0) {
					discard;
					return;
				}
			#else
				if (depth >= 1.0) {
					discard;
					return;
				}
			#endif
			
			vec3 viewPos = getViewPosition(vUv, depth);
			vec3 viewNormal = getViewNormal(vUv);

			float radiusToUse = radius;
			float distanceFalloffToUse = thickness;
			#if SCREEN_SPACE_RADIUS == 1
				float radiusScale = getViewPosition(vec2(0.5 + float(SCREEN_SPACE_RADIUS_SCALE) / resolution.x, 0.0), depth).x;
				radiusToUse *= radiusScale;
				distanceFalloffToUse *= radiusScale;
			#endif

			#if SCENE_CLIP_BOX == 1
				vec3 worldPos = (cameraWorldMatrix * vec4(viewPos, 1.0)).xyz;
				float boxDistance = length(max(vec3(0.0), max(sceneBoxMin - worldPos, worldPos - sceneBoxMax)));
				if (boxDistance > radiusToUse) {
					discard;
					return;
				}
			#endif

			vec2 noiseResolution = vec2(textureSize(tNoise, 0));
			vec2 noiseUv = vUv * resolution / noiseResolution;
			vec4 noiseTexel = textureLod(tNoise, noiseUv, 0.0);
			vec3 randomVec = noiseTexel.xyz * 2.0 - 1.0;
			vec3 tangent = normalize(vec3(randomVec.xy, 0.));
			vec3 bitangent = vec3(-tangent.y, tangent.x, 0.);
			mat3 kernelMatrix = mat3(tangent, bitangent, vec3(0., 0., 1.));

			const int DIRECTIONS = SAMPLES < 30 ? 3 : 5;
			const int STEPS = (SAMPLES + DIRECTIONS - 1) / DIRECTIONS;
			float ao = 0.0;
			for (int i = 0; i < DIRECTIONS; ++i) {

				float angle = float(i) / float(DIRECTIONS) * PI;
				vec4 sampleDir = vec4(cos(angle), sin(angle), 0., 0.5 + 0.5 * noiseTexel.w);
				sampleDir.xyz = normalize(kernelMatrix * sampleDir.xyz);

				#if PERSPECTIVE_CAMERA == 1
					vec3 viewDir = normalize(-viewPos.xyz);
				#else
					vec3 viewDir = vec3(0., 0., 1.);
				#endif
				vec3 sliceBitangent = normalize(cross(sampleDir.xyz, viewDir));
				vec3 sliceTangent = cross(sliceBitangent, viewDir);
				vec3 normalInSlice = normalize(viewNormal - sliceBitangent * dot(viewNormal, sliceBitangent));

				vec3 tangentToNormalInSlice = cross(normalInSlice, sliceBitangent);
				vec2 cosHorizons = vec2(dot(viewDir, tangentToNormalInSlice), dot(viewDir, -tangentToNormalInSlice));

				for (int j = 0; j < STEPS; ++j) {
					vec3 sampleViewOffset = sampleDir.xyz * radiusToUse * sampleDir.w * pow(float(j + 1) / float(STEPS), distanceExponent);

					vec3 sampleSceneUvDepth = getSceneUvAndDepth(viewPos + sampleViewOffset);
					vec3 sampleSceneViewPos = getViewPosition(sampleSceneUvDepth.xy, sampleSceneUvDepth.z);
					vec3 viewDelta = sampleSceneViewPos - viewPos;
					if (abs(viewDelta.z) < thickness) {
						float sampleCosHorizon = dot(viewDir, normalize(viewDelta));
						cosHorizons.x += max(0., (sampleCosHorizon - cosHorizons.x) * mix(1., 2. / float(j + 2), distanceFallOff));
					}

					sampleSceneUvDepth = getSceneUvAndDepth(viewPos - sampleViewOffset);
					sampleSceneViewPos = getViewPosition(sampleSceneUvDepth.xy, sampleSceneUvDepth.z);
					viewDelta = sampleSceneViewPos - viewPos;
					if (abs(viewDelta.z) < thickness) {
						float sampleCosHorizon = dot(viewDir, normalize(viewDelta));
						cosHorizons.y += max(0., (sampleCosHorizon - cosHorizons.y) * mix(1., 2. / float(j + 2), distanceFallOff));
					}
				}

				vec2 sinHorizons = sqrt(1. - cosHorizons * cosHorizons);
				float nx = dot(normalInSlice, sliceTangent);
				float ny = dot(normalInSlice, viewDir);
				float nxb = 1. / 2. * (acos(cosHorizons.y) - acos(cosHorizons.x) + sinHorizons.x * cosHorizons.x - sinHorizons.y * cosHorizons.y);
				float nyb = 1. / 2. * (2. - cosHorizons.x * cosHorizons.x - cosHorizons.y * cosHorizons.y);
				float occlusion = nx * nxb + ny * nyb;
				ao += occlusion;
			}

			ao = clamp(ao / float(DIRECTIONS), 0., 1.);
		#if SCENE_CLIP_BOX == 1
			ao = mix(ao, 1., smoothstep(0., radiusToUse, boxDistance));
		#endif
			ao = pow(ao, scale);

			gl_FragColor = FRAGMENT_OUTPUT;
		}`},lt={name:"GTAODepthShader",defines:{PERSPECTIVE_CAMERA:1},uniforms:{tDepth:{value:null},cameraNear:{value:null},cameraFar:{value:null}},vertexShader:`
		varying vec2 vUv;

		void main() {
			vUv = uv;
			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
		}`,fragmentShader:`
		uniform sampler2D tDepth;
		uniform float cameraNear;
		uniform float cameraFar;
		varying vec2 vUv;

		#include <packing>

		float getLinearDepth( const in vec2 screenPosition ) {
			#if PERSPECTIVE_CAMERA == 1
				float fragCoordZ = texture2D( tDepth, screenPosition ).x;
				float viewZ = perspectiveDepthToViewZ( fragCoordZ, cameraNear, cameraFar );
				return viewZToOrthographicDepth( viewZ, cameraNear, cameraFar );
			#else
				return texture2D( tDepth, screenPosition ).x;
			#endif
		}

		void main() {
			float depth = getLinearDepth( vUv );
			gl_FragColor = vec4( vec3( 1.0 - depth ), 1.0 );

		}`},Ct={name:"GTAOBlendShader",uniforms:{tDiffuse:{value:null},intensity:{value:1}},vertexShader:`
		varying vec2 vUv;

		void main() {
			vUv = uv;
			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
		}`,fragmentShader:`
		uniform float intensity;
		uniform sampler2D tDiffuse;
		varying vec2 vUv;

		void main() {
			vec4 texel = texture2D( tDiffuse, vUv );
			gl_FragColor = vec4(mix(vec3(1.), texel.rgb, intensity), texel.a);
		}`};function hi(c=5){let e=Math.floor(c)%2===0?Math.floor(c)+1:Math.floor(c),t=bo(e),s=t.length,i=new Uint8Array(s*4);for(let a=0;a<s;++a){let n=t[a],r=2*Math.PI*n/s,l=new ks(Math.cos(r),Math.sin(r),0).normalize();i[a*4]=(l.x*.5+.5)*255,i[a*4+1]=(l.y*.5+.5)*255,i[a*4+2]=127,i[a*4+3]=255}let o=new vo(i,e,e);return o.wrapS=li,o.wrapT=li,o.needsUpdate=!0,o}function bo(c){let e=Math.floor(c)%2===0?Math.floor(c)+1:Math.floor(c),t=e*e,s=Array(t).fill(0),i=Math.floor(e/2),o=e-1;for(let a=1;a<=t;){if(i===-1&&o===e?(o=e-2,i=0):(o===e&&(o=0),i<0&&(i=e-1)),s[i*e+o]!==0){o-=2,i++;continue}else s[i*e+o]=a++;o++,i--}return s}import{Matrix4 as wo,Vector2 as Mo,Vector3 as yo}from"three";var ht={name:"PoissonDenoiseShader",defines:{SAMPLES:16,SAMPLE_VECTORS:zs(16,2,1),NORMAL_VECTOR_TYPE:1},uniforms:{tDiffuse:{value:null},tNormal:{value:null},tDepth:{value:null},tNoise:{value:null},resolution:{value:new Mo},cameraProjectionMatrixInverse:{value:new wo},lumaPhi:{value:5},depthPhi:{value:5},normalPhi:{value:5},radius:{value:4},index:{value:0}},vertexShader:`

		varying vec2 vUv;

		void main() {
			vUv = uv;
			gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
		}`,fragmentShader:`

		varying vec2 vUv;

		uniform sampler2D tDiffuse;
		uniform sampler2D tNormal;
		uniform sampler2D tDepth;
		uniform sampler2D tNoise;
		uniform vec2 resolution;
		uniform mat4 cameraProjectionMatrixInverse;
		uniform float lumaPhi;
		uniform float depthPhi;
		uniform float normalPhi;
		uniform float radius;
		uniform int index;

		#include <common>
		#include <packing>

		#ifndef SAMPLE_LUMINANCE
		#define SAMPLE_LUMINANCE dot(vec3(0.2125, 0.7154, 0.0721), a)
		#endif

		#ifndef FRAGMENT_OUTPUT
		#define FRAGMENT_OUTPUT vec4(denoised, 1.)
		#endif

		float getLuminance(const in vec3 a) {
			return SAMPLE_LUMINANCE;
		}

		const vec3 poissonDisk[SAMPLES] = SAMPLE_VECTORS;

		vec3 getViewPosition( const in vec2 screenPosition, const in float depth ) {
			#ifdef USE_REVERSED_DEPTH_BUFFER
				vec4 clipSpacePosition = vec4( vec2( screenPosition ) * 2.0 - 1.0, depth, 1.0 );
			#else
				vec4 clipSpacePosition = vec4( vec3( screenPosition, depth ) * 2.0 - 1.0, 1.0 );
			#endif
			vec4 viewSpacePosition = cameraProjectionMatrixInverse * clipSpacePosition;
			return viewSpacePosition.xyz / viewSpacePosition.w;
		}

		float getDepth(const vec2 uv) {
			return textureLod(tDepth, uv.xy, 0.0).r;
		}

		float fetchDepth(const ivec2 uv) {
			return texelFetch(tDepth, uv.xy, 0).r;
		}

		vec3 computeNormalFromDepth(const vec2 uv) {
			vec2 size = vec2(textureSize(tDepth, 0));
			ivec2 p = ivec2(uv * size);
			float c0 = fetchDepth(p);
			float l2 = fetchDepth(p - ivec2(2, 0));
			float l1 = fetchDepth(p - ivec2(1, 0));
			float r1 = fetchDepth(p + ivec2(1, 0));
			float r2 = fetchDepth(p + ivec2(2, 0));
			float b2 = fetchDepth(p - ivec2(0, 2));
			float b1 = fetchDepth(p - ivec2(0, 1));
			float t1 = fetchDepth(p + ivec2(0, 1));
			float t2 = fetchDepth(p + ivec2(0, 2));
			float dl = abs((2.0 * l1 - l2) - c0);
			float dr = abs((2.0 * r1 - r2) - c0);
			float db = abs((2.0 * b1 - b2) - c0);
			float dt = abs((2.0 * t1 - t2) - c0);
			vec3 ce = getViewPosition(uv, c0).xyz;
			vec3 dpdx = (dl < dr) ?  ce - getViewPosition((uv - vec2(1.0 / size.x, 0.0)), l1).xyz
									: -ce + getViewPosition((uv + vec2(1.0 / size.x, 0.0)), r1).xyz;
			vec3 dpdy = (db < dt) ?  ce - getViewPosition((uv - vec2(0.0, 1.0 / size.y)), b1).xyz
									: -ce + getViewPosition((uv + vec2(0.0, 1.0 / size.y)), t1).xyz;
			return normalize(cross(dpdx, dpdy));
		}

		vec3 getViewNormal(const vec2 uv) {
		#if NORMAL_VECTOR_TYPE == 2
			return normalize(textureLod(tNormal, uv, 0.).rgb);
		#elif NORMAL_VECTOR_TYPE == 1
			return unpackRGBToNormal(textureLod(tNormal, uv, 0.).rgb);
		#else
			return computeNormalFromDepth(uv);
		#endif
		}

		void denoiseSample(in vec3 center, in vec3 viewNormal, in vec3 viewPos, in vec2 sampleUv, inout vec3 denoised, inout float totalWeight) {
			vec4 sampleTexel = textureLod(tDiffuse, sampleUv, 0.0);
			float sampleDepth = getDepth(sampleUv);
			vec3 sampleNormal = getViewNormal(sampleUv);
			vec3 neighborColor = sampleTexel.rgb;
			vec3 viewPosSample = getViewPosition(sampleUv, sampleDepth);

			float normalDiff = dot(viewNormal, sampleNormal);
			float normalSimilarity = pow(max(normalDiff, 0.), normalPhi);
			float lumaDiff = abs(getLuminance(neighborColor) - getLuminance(center));
			float lumaSimilarity = max(1.0 - lumaDiff / lumaPhi, 0.0);
			float depthDiff = abs(dot(viewPos - viewPosSample, viewNormal));
			float depthSimilarity = max(1. - depthDiff / depthPhi, 0.);
			float w = lumaSimilarity * depthSimilarity * normalSimilarity;

			denoised += w * neighborColor;
			totalWeight += w;
		}

		void main() {
			float depth = getDepth(vUv.xy);
			vec3 viewNormal = getViewNormal(vUv);
			if (depth == 1. || dot(viewNormal, viewNormal) == 0.) {
				discard;
				return;
			}
			vec4 texel = textureLod(tDiffuse, vUv, 0.0);
			vec3 center = texel.rgb;
			vec3 viewPos = getViewPosition(vUv, depth);

			vec2 noiseResolution = vec2(textureSize(tNoise, 0));
			vec2 noiseUv = vUv * resolution / noiseResolution;
			vec4 noiseTexel = textureLod(tNoise, noiseUv, 0.0);
      		vec2 noiseVec = vec2(sin(noiseTexel[index % 4] * 2. * PI), cos(noiseTexel[index % 4] * 2. * PI));
    		mat2 rotationMatrix = mat2(noiseVec.x, -noiseVec.y, noiseVec.x, noiseVec.y);

			float totalWeight = 1.0;
			vec3 denoised = texel.rgb;
			for (int i = 0; i < SAMPLES; i++) {
				vec3 sampleDir = poissonDisk[i];
				vec2 offset = rotationMatrix * (sampleDir.xy * (1. + sampleDir.z * (radius - 1.)) / resolution);
				vec2 sampleUv = vUv + offset;
				denoiseSample(center, viewNormal, viewPos, sampleUv, denoised, totalWeight);
			}

			if (totalWeight > 0.) {
				denoised /= totalWeight;
			}
			gl_FragColor = FRAGMENT_OUTPUT;
		}`};function zs(c,e,t){let s=To(c,e,t),i="vec3[SAMPLES](";for(let o=0;o<c;o++){let a=s[o];i+=`vec3(${a.x}, ${a.y}, ${a.z})${o<c-1?",":")"}`}return i}function To(c,e,t){let s=[];for(let i=0;i<c;i++){let o=2*Math.PI*e*i/c,a=Math.pow(i/(c-1),t);s.push(new yo(Math.cos(o),Math.sin(o),a))}return s}var Dt=class{constructor(e=Math){this.grad3=[[1,1,0],[-1,1,0],[1,-1,0],[-1,-1,0],[1,0,1],[-1,0,1],[1,0,-1],[-1,0,-1],[0,1,1],[0,-1,1],[0,1,-1],[0,-1,-1]],this.grad4=[[0,1,1,1],[0,1,1,-1],[0,1,-1,1],[0,1,-1,-1],[0,-1,1,1],[0,-1,1,-1],[0,-1,-1,1],[0,-1,-1,-1],[1,0,1,1],[1,0,1,-1],[1,0,-1,1],[1,0,-1,-1],[-1,0,1,1],[-1,0,1,-1],[-1,0,-1,1],[-1,0,-1,-1],[1,1,0,1],[1,1,0,-1],[1,-1,0,1],[1,-1,0,-1],[-1,1,0,1],[-1,1,0,-1],[-1,-1,0,1],[-1,-1,0,-1],[1,1,1,0],[1,1,-1,0],[1,-1,1,0],[1,-1,-1,0],[-1,1,1,0],[-1,1,-1,0],[-1,-1,1,0],[-1,-1,-1,0]],this.p=[];for(let t=0;t<256;t++)this.p[t]=Math.floor(e.random()*256);this.perm=[];for(let t=0;t<512;t++)this.perm[t]=this.p[t&255];this.simplex=[[0,1,2,3],[0,1,3,2],[0,0,0,0],[0,2,3,1],[0,0,0,0],[0,0,0,0],[0,0,0,0],[1,2,3,0],[0,2,1,3],[0,0,0,0],[0,3,1,2],[0,3,2,1],[0,0,0,0],[0,0,0,0],[0,0,0,0],[1,3,2,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[1,2,0,3],[0,0,0,0],[1,3,0,2],[0,0,0,0],[0,0,0,0],[0,0,0,0],[2,3,0,1],[2,3,1,0],[1,0,2,3],[1,0,3,2],[0,0,0,0],[0,0,0,0],[0,0,0,0],[2,0,3,1],[0,0,0,0],[2,1,3,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[2,0,1,3],[0,0,0,0],[0,0,0,0],[0,0,0,0],[3,0,1,2],[3,0,2,1],[0,0,0,0],[3,1,2,0],[2,1,0,3],[0,0,0,0],[0,0,0,0],[0,0,0,0],[3,1,0,2],[0,0,0,0],[3,2,0,1],[3,2,1,0]]}noise(e,t){let s,i,o,a=.5*(Math.sqrt(3)-1),n=(e+t)*a,r=Math.floor(e+n),l=Math.floor(t+n),h=(3-Math.sqrt(3))/6,u=(r+l)*h,d=r-u,f=l-u,p=e-d,g=t-f,v,m;p>g?(v=1,m=0):(v=0,m=1);let x=p-v+h,b=g-m+h,w=p-1+2*h,M=g-1+2*h,E=r&255,S=l&255,T=this.perm[E+this.perm[S]]%12,R=this.perm[E+v+this.perm[S+m]]%12,z=this.perm[E+1+this.perm[S+1]]%12,k=.5-p*p-g*g;k<0?s=0:(k*=k,s=k*k*this._dot(this.grad3[T],p,g));let P=.5-x*x-b*b;P<0?i=0:(P*=P,i=P*P*this._dot(this.grad3[R],x,b));let _=.5-w*w-M*M;return _<0?o=0:(_*=_,o=_*_*this._dot(this.grad3[z],w,M)),70*(s+i+o)}noise3d(e,t,s){let i,o,a,n,l=(e+t+s)*.3333333333333333,h=Math.floor(e+l),u=Math.floor(t+l),d=Math.floor(s+l),f=1/6,p=(h+u+d)*f,g=h-p,v=u-p,m=d-p,x=e-g,b=t-v,w=s-m,M,E,S,T,R,z;x>=b?b>=w?(M=1,E=0,S=0,T=1,R=1,z=0):x>=w?(M=1,E=0,S=0,T=1,R=0,z=1):(M=0,E=0,S=1,T=1,R=0,z=1):b<w?(M=0,E=0,S=1,T=0,R=1,z=1):x<w?(M=0,E=1,S=0,T=0,R=1,z=1):(M=0,E=1,S=0,T=1,R=1,z=0);let k=x-M+f,P=b-E+f,_=w-S+f,V=x-T+2*f,oe=b-R+2*f,ee=w-z+2*f,me=x-1+3*f,Oe=b-1+3*f,$=w-1+3*f,ze=h&255,Ae=u&255,Pe=d&255,xt=this.perm[ze+this.perm[Ae+this.perm[Pe]]]%12,bt=this.perm[ze+M+this.perm[Ae+E+this.perm[Pe+S]]]%12,wt=this.perm[ze+T+this.perm[Ae+R+this.perm[Pe+z]]]%12,Mt=this.perm[ze+1+this.perm[Ae+1+this.perm[Pe+1]]]%12,ge=.6-x*x-b*b-w*w;ge<0?i=0:(ge*=ge,i=ge*ge*this._dot3(this.grad3[xt],x,b,w));let ve=.6-k*k-P*P-_*_;ve<0?o=0:(ve*=ve,o=ve*ve*this._dot3(this.grad3[bt],k,P,_));let xe=.6-V*V-oe*oe-ee*ee;xe<0?a=0:(xe*=xe,a=xe*xe*this._dot3(this.grad3[wt],V,oe,ee));let be=.6-me*me-Oe*Oe-$*$;return be<0?n=0:(be*=be,n=be*be*this._dot3(this.grad3[Mt],me,Oe,$)),32*(i+o+a+n)}noise4d(e,t,s,i){let o=this.grad4,a=this.simplex,n=this.perm,r=(Math.sqrt(5)-1)/4,l=(5-Math.sqrt(5))/20,h,u,d,f,p,g=(e+t+s+i)*r,v=Math.floor(e+g),m=Math.floor(t+g),x=Math.floor(s+g),b=Math.floor(i+g),w=(v+m+x+b)*l,M=v-w,E=m-w,S=x-w,T=b-w,R=e-M,z=t-E,k=s-S,P=i-T,_=R>z?32:0,V=R>k?16:0,oe=z>k?8:0,ee=R>P?4:0,me=z>P?2:0,Oe=k>P?1:0,$=_+V+oe+ee+me+Oe,ze=a[$][0]>=3?1:0,Ae=a[$][1]>=3?1:0,Pe=a[$][2]>=3?1:0,xt=a[$][3]>=3?1:0,bt=a[$][0]>=2?1:0,wt=a[$][1]>=2?1:0,Mt=a[$][2]>=2?1:0,ge=a[$][3]>=2?1:0,ve=a[$][0]>=1?1:0,xe=a[$][1]>=1?1:0,be=a[$][2]>=1?1:0,Os=a[$][3]>=1?1:0,is=R-ze+l,os=z-Ae+l,as=k-Pe+l,ns=P-xt+l,rs=R-bt+2*l,ls=z-wt+2*l,hs=k-Mt+2*l,cs=P-ge+2*l,us=R-ve+3*l,ds=z-xe+3*l,ps=k-be+3*l,fs=P-Os+3*l,ms=R-1+4*l,gs=z-1+4*l,vs=k-1+4*l,xs=P-1+4*l,Ve=v&255,je=m&255,We=x&255,qe=b&255,Pi=n[Ve+n[je+n[We+n[qe]]]]%32,Hi=n[Ve+ze+n[je+Ae+n[We+Pe+n[qe+xt]]]]%32,Ci=n[Ve+bt+n[je+wt+n[We+Mt+n[qe+ge]]]]%32,Di=n[Ve+ve+n[je+xe+n[We+be+n[qe+Os]]]]%32,_i=n[Ve+1+n[je+1+n[We+1+n[qe+1]]]]%32,Ye=.6-R*R-z*z-k*k-P*P;Ye<0?h=0:(Ye*=Ye,h=Ye*Ye*this._dot4(o[Pi],R,z,k,P));let Ke=.6-is*is-os*os-as*as-ns*ns;Ke<0?u=0:(Ke*=Ke,u=Ke*Ke*this._dot4(o[Hi],is,os,as,ns));let $e=.6-rs*rs-ls*ls-hs*hs-cs*cs;$e<0?d=0:($e*=$e,d=$e*$e*this._dot4(o[Ci],rs,ls,hs,cs));let Xe=.6-us*us-ds*ds-ps*ps-fs*fs;Xe<0?f=0:(Xe*=Xe,f=Xe*Xe*this._dot4(o[Di],us,ds,ps,fs));let Qe=.6-ms*ms-gs*gs-vs*vs-xs*xs;return Qe<0?p=0:(Qe*=Qe,p=Qe*Qe*this._dot4(o[_i],ms,gs,vs,xs)),27*(h+u+d+f+p)}_dot(e,t,s){return e[0]*t+e[1]*s}_dot3(e,t,s,i){return e[0]*t+e[1]*s+e[2]*i}_dot4(e,t,s,i,o){return e[0]*t+e[1]*s+e[2]*i+e[3]*o}};var Ne=class c extends Z{constructor(e,t,s=512,i=512,o,a,n){super(),this.width=s,this.height=i,this.clear=!0,this.camera=t,this.scene=e,this.output=0,this._renderGBuffer=!0,this._visibilityCache=[],this.blendIntensity=1,this.pdRings=2,this.pdRadiusExponent=2,this.pdSamples=16,this.gtaoNoiseTexture=hi(),this.pdNoiseTexture=this._generateNoise(),this.gtaoRenderTarget=new mi(this.width,this.height,{type:di,depthBuffer:!1}),this.pdRenderTarget=this.gtaoRenderTarget.clone(),this.gtaoMaterial=new ct({defines:Object.assign({},rt.defines),uniforms:ut.clone(rt.uniforms),vertexShader:rt.vertexShader,fragmentShader:rt.fragmentShader,blending:Ee,depthTest:!1,depthWrite:!1}),this.gtaoMaterial.defines.PERSPECTIVE_CAMERA=this.camera.isPerspectiveCamera?1:0,this.gtaoMaterial.uniforms.tNoise.value=this.gtaoNoiseTexture,this.gtaoMaterial.uniforms.resolution.value.set(this.width,this.height),this.gtaoMaterial.uniforms.cameraNear.value=this.camera.near,this.gtaoMaterial.uniforms.cameraFar.value=this.camera.far,this.normalMaterial=new Ao,this.normalMaterial.blending=Ee,this.pdMaterial=new ct({defines:Object.assign({},ht.defines),uniforms:ut.clone(ht.uniforms),vertexShader:ht.vertexShader,fragmentShader:ht.fragmentShader,depthTest:!1,depthWrite:!1}),this.pdMaterial.uniforms.tDiffuse.value=this.gtaoRenderTarget.texture,this.pdMaterial.uniforms.tNoise.value=this.pdNoiseTexture,this.pdMaterial.uniforms.resolution.value.set(this.width,this.height),this.pdMaterial.uniforms.lumaPhi.value=10,this.pdMaterial.uniforms.depthPhi.value=2,this.pdMaterial.uniforms.normalPhi.value=3,this.pdMaterial.uniforms.radius.value=8,this.depthRenderMaterial=new ct({defines:Object.assign({},lt.defines),uniforms:ut.clone(lt.uniforms),vertexShader:lt.vertexShader,fragmentShader:lt.fragmentShader,blending:Ee}),this.depthRenderMaterial.uniforms.cameraNear.value=this.camera.near,this.depthRenderMaterial.uniforms.cameraFar.value=this.camera.far,this.copyMaterial=new ct({uniforms:ut.clone(he.uniforms),vertexShader:he.vertexShader,fragmentShader:he.fragmentShader,transparent:!0,depthTest:!1,depthWrite:!1,blendSrc:ui,blendDst:Lt,blendEquation:_t,blendSrcAlpha:ci,blendDstAlpha:Lt,blendEquationAlpha:_t}),this.blendMaterial=new ct({uniforms:ut.clone(Ct.uniforms),vertexShader:Ct.vertexShader,fragmentShader:Ct.fragmentShader,transparent:!0,depthTest:!1,depthWrite:!1,blending:So,blendSrc:ui,blendDst:Lt,blendEquation:_t,blendSrcAlpha:ci,blendDstAlpha:Lt,blendEquationAlpha:_t}),this._fsQuad=new ce(null),this._originalClearColor=new Eo,this.setGBuffer(o?o.depthTexture:void 0,o?o.normalTexture:void 0),a!==void 0&&this.updateGtaoMaterial(a),n!==void 0&&this.updatePdMaterial(n)}setSize(e,t){this.width=e,this.height=t,this.gtaoRenderTarget.setSize(e,t),this.normalRenderTarget&&this.normalRenderTarget.setSize(e,t),this.pdRenderTarget.setSize(e,t),this.gtaoMaterial.uniforms.resolution.value.set(e,t),this.gtaoMaterial.uniforms.cameraProjectionMatrix.value.copy(this.camera.projectionMatrix),this.gtaoMaterial.uniforms.cameraProjectionMatrixInverse.value.copy(this.camera.projectionMatrixInverse),this.pdMaterial.uniforms.resolution.value.set(e,t),this.pdMaterial.uniforms.cameraProjectionMatrixInverse.value.copy(this.camera.projectionMatrixInverse)}dispose(){this.gtaoNoiseTexture.dispose(),this.pdNoiseTexture.dispose(),this.normalRenderTarget&&this.normalRenderTarget.dispose(),this.gtaoRenderTarget.dispose(),this.pdRenderTarget.dispose(),this.normalMaterial.dispose(),this.pdMaterial.dispose(),this.copyMaterial.dispose(),this.depthRenderMaterial.dispose(),this._fsQuad.dispose()}get gtaoMap(){return this.pdRenderTarget.texture}setGBuffer(e,t){if(e!==void 0)this.depthTexture=e,this.normalTexture=t,this._renderGBuffer=!1;else{if(this.normalRenderTarget===void 0){let i=new ko;i.format=zo,i.type=Co,this.normalRenderTarget=new mi(this.width,this.height,{minFilter:pi,magFilter:pi,type:di,depthTexture:i})}this.depthTexture=this.normalRenderTarget.depthTexture,this.normalTexture=this.normalRenderTarget.texture,this._renderGBuffer=!0}let s=this.normalTexture?1:0;this.gtaoMaterial.defines.NORMAL_VECTOR_TYPE=s,this.gtaoMaterial.needsUpdate=!0,this.gtaoMaterial.uniforms.tNormal.value=this.normalTexture,this.gtaoMaterial.uniforms.tDepth.value=this.depthTexture,this.pdMaterial.defines.NORMAL_VECTOR_TYPE=s,this.pdMaterial.needsUpdate=!0,this.pdMaterial.uniforms.tNormal.value=this.normalTexture,this.pdMaterial.uniforms.tDepth.value=this.depthTexture,this.depthRenderMaterial.uniforms.tDepth.value=this.depthTexture}setSceneClipBox(e){e?(this.gtaoMaterial.needsUpdate=this.gtaoMaterial.defines.SCENE_CLIP_BOX!==1,this.gtaoMaterial.defines.SCENE_CLIP_BOX=1,this.gtaoMaterial.uniforms.sceneBoxMin.value.copy(e.min),this.gtaoMaterial.uniforms.sceneBoxMax.value.copy(e.max)):(this.gtaoMaterial.needsUpdate=this.gtaoMaterial.defines.SCENE_CLIP_BOX===0,this.gtaoMaterial.defines.SCENE_CLIP_BOX=0)}updateGtaoMaterial(e){e.radius!==void 0&&(this.gtaoMaterial.uniforms.radius.value=e.radius),e.distanceExponent!==void 0&&(this.gtaoMaterial.uniforms.distanceExponent.value=e.distanceExponent),e.thickness!==void 0&&(this.gtaoMaterial.uniforms.thickness.value=e.thickness),e.distanceFallOff!==void 0&&(this.gtaoMaterial.uniforms.distanceFallOff.value=e.distanceFallOff,this.gtaoMaterial.needsUpdate=!0),e.scale!==void 0&&(this.gtaoMaterial.uniforms.scale.value=e.scale),e.samples!==void 0&&e.samples!==this.gtaoMaterial.defines.SAMPLES&&(this.gtaoMaterial.defines.SAMPLES=e.samples,this.gtaoMaterial.needsUpdate=!0),e.screenSpaceRadius!==void 0&&(e.screenSpaceRadius?1:0)!==this.gtaoMaterial.defines.SCREEN_SPACE_RADIUS&&(this.gtaoMaterial.defines.SCREEN_SPACE_RADIUS=e.screenSpaceRadius?1:0,this.gtaoMaterial.needsUpdate=!0)}updatePdMaterial(e){let t=!1;e.lumaPhi!==void 0&&(this.pdMaterial.uniforms.lumaPhi.value=e.lumaPhi),e.depthPhi!==void 0&&(this.pdMaterial.uniforms.depthPhi.value=e.depthPhi),e.normalPhi!==void 0&&(this.pdMaterial.uniforms.normalPhi.value=e.normalPhi),e.radius!==void 0&&e.radius!==this.radius&&(this.pdMaterial.uniforms.radius.value=e.radius),e.radiusExponent!==void 0&&e.radiusExponent!==this.pdRadiusExponent&&(this.pdRadiusExponent=e.radiusExponent,t=!0),e.rings!==void 0&&e.rings!==this.pdRings&&(this.pdRings=e.rings,t=!0),e.samples!==void 0&&e.samples!==this.pdSamples&&(this.pdSamples=e.samples,t=!0),t&&(this.pdMaterial.defines.SAMPLES=this.pdSamples,this.pdMaterial.defines.SAMPLE_VECTORS=zs(this.pdSamples,this.pdRings,this.pdRadiusExponent),this.pdMaterial.needsUpdate=!0)}render(e,t,s){switch(this._renderGBuffer&&(this._overrideVisibility(),this._renderOverride(e,this.normalMaterial,this.normalRenderTarget,7829503,1),this._restoreVisibility()),this.gtaoMaterial.uniforms.cameraNear.value=this.camera.near,this.gtaoMaterial.uniforms.cameraFar.value=this.camera.far,this.gtaoMaterial.uniforms.cameraProjectionMatrix.value.copy(this.camera.projectionMatrix),this.gtaoMaterial.uniforms.cameraProjectionMatrixInverse.value.copy(this.camera.projectionMatrixInverse),this.gtaoMaterial.uniforms.cameraWorldMatrix.value.copy(this.camera.matrixWorld),this._renderPass(e,this.gtaoMaterial,this.gtaoRenderTarget,16777215,1),this.pdMaterial.uniforms.cameraProjectionMatrixInverse.value.copy(this.camera.projectionMatrixInverse),this._renderPass(e,this.pdMaterial,this.pdRenderTarget,16777215,1),this.output){case c.OUTPUT.Off:break;case c.OUTPUT.Diffuse:this.copyMaterial.uniforms.tDiffuse.value=s.texture,this.copyMaterial.blending=Ee,this._renderPass(e,this.copyMaterial,this.renderToScreen?null:t);break;case c.OUTPUT.AO:this.copyMaterial.uniforms.tDiffuse.value=this.gtaoRenderTarget.texture,this.copyMaterial.blending=Ee,this._renderPass(e,this.copyMaterial,this.renderToScreen?null:t);break;case c.OUTPUT.Denoise:this.copyMaterial.uniforms.tDiffuse.value=this.pdRenderTarget.texture,this.copyMaterial.blending=Ee,this._renderPass(e,this.copyMaterial,this.renderToScreen?null:t);break;case c.OUTPUT.Depth:this.depthRenderMaterial.uniforms.cameraNear.value=this.camera.near,this.depthRenderMaterial.uniforms.cameraFar.value=this.camera.far,this._renderPass(e,this.depthRenderMaterial,this.renderToScreen?null:t);break;case c.OUTPUT.Normal:this.copyMaterial.uniforms.tDiffuse.value=this.normalTexture||null,this.copyMaterial.blending=Ee,this._renderPass(e,this.copyMaterial,this.renderToScreen?null:t);break;case c.OUTPUT.Default:this.copyMaterial.uniforms.tDiffuse.value=s.texture,this.copyMaterial.blending=Ee,this._renderPass(e,this.copyMaterial,this.renderToScreen?null:t),this.blendMaterial.uniforms.intensity.value=this.blendIntensity,this.blendMaterial.uniforms.tDiffuse.value=this.pdRenderTarget.texture,this._renderPass(e,this.blendMaterial,this.renderToScreen?null:t);break;default:console.warn("THREE.GTAOPass: Unknown output type.")}}_renderPass(e,t,s,i,o){e.getClearColor(this._originalClearColor);let a=e.getClearAlpha(),n=e.autoClear;e.setRenderTarget(s),e.autoClear=!1,i!=null&&(e.setClearColor(i),e.setClearAlpha(o||0),e.clear()),this._fsQuad.material=t,this._fsQuad.render(e),e.autoClear=n,e.setClearColor(this._originalClearColor),e.setClearAlpha(a)}_renderOverride(e,t,s,i,o){e.getClearColor(this._originalClearColor);let a=e.getClearAlpha(),n=e.autoClear;e.setRenderTarget(s),e.autoClear=!1,i=t.clearColor||i,o=t.clearAlpha||o,i!=null&&(e.setClearColor(i),e.setClearAlpha(o||0),e.clear()),this.scene.overrideMaterial=t,e.render(this.scene,this.camera),this.scene.overrideMaterial=null,e.autoClear=n,e.setClearColor(this._originalClearColor),e.setClearAlpha(a)}_overrideVisibility(){let e=this.scene,t=this._visibilityCache;e.traverse(function(s){(s.isPoints||s.isLine||s.isLine2)&&s.visible&&(s.visible=!1,t.push(s))})}_restoreVisibility(){let e=this._visibilityCache;for(let t=0;t<e.length;t++)e[t].visible=!0;e.length=0}_generateNoise(e=64){let t=new Dt,s=e*e*4,i=new Uint8Array(s);for(let a=0;a<e;a++)for(let n=0;n<e;n++){let r=a,l=n;i[(a*e+n)*4]=(t.noise(r,l)*.5+.5)*255,i[(a*e+n)*4+1]=(t.noise(r+e,l)*.5+.5)*255,i[(a*e+n)*4+2]=(t.noise(r,l+e)*.5+.5)*255,i[(a*e+n)*4+3]=(t.noise(r+e,l+e)*.5+.5)*255}let o=new Ro(i,e,e,Po,Ho);return o.wrapS=fi,o.wrapT=fi,o.needsUpdate=!0,o}};Ne.OUTPUT={Off:-1,Default:0,Diffuse:1,Depth:2,Normal:3,AO:4,Denoise:5};var Do={uniforms:{tDiffuse:{value:null},uVig:{value:.32},uSat:{value:1.2},uCon:{value:1.16},uHurt:{value:0},uTime:{value:0},uWarm:{value:0}},vertexShader:"varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",fragmentShader:`
    uniform sampler2D tDiffuse; uniform float uVig, uSat, uCon, uHurt, uTime, uWarm; varying vec2 vUv;
    void main(){
      vec3 c = texture2D(tDiffuse, vUv).rgb;
      float l = dot(c, vec3(0.299, 0.587, 0.114));
      c = mix(vec3(l), c, uSat);
      c = (c - 0.5) * uCon + 0.5;
      c *= vec3(1.0 + uWarm * 0.06, 1.0, 1.0 - uWarm * 0.05);
      vec2 d = vUv - 0.5;
      float r = dot(d, d);
      c *= 1.0 - uVig * smoothstep(0.08, 0.55, r);
      float edge = smoothstep(0.12, 0.5, r) * uHurt * (0.75 + 0.25 * sin(uTime * 9.0));
      c = mix(c, vec3(0.55, 0.0, 0.03), edge);
      // a little film grain against banding
      float n = fract(sin(dot(vUv * (uTime + 1.0), vec2(12.9898, 78.233))) * 43758.5453);
      c += (n - 0.5) / 255.0 * 2.0;
      gl_FragColor = vec4(c, 1.0);
    }`},gi=[190,280,380,520],Ft=class{constructor(e,t){this.g=e,this.sky=t;let s=e.three;this.renderer=s.renderer,this.scene=s.scene,this.camera=s.camera,this.camera.near=.08,this.quality=-1,this.grade=new ye(Do),this.onQualityFns=[],s.setPipeline(i=>this.render(i)),s.onQuality(i=>this.build(i)),new ResizeObserver(()=>this.resize()).observe(e.root)}size(){let e=this.g.root.clientWidth||innerWidth,t=this.g.root.clientHeight||innerHeight;return[e,t]}build(e){this.quality=e;let t=this.renderer,s=devicePixelRatio||1,i=[.75,1,Math.min(s,1.25),Math.min(s,1.5)][e];t.setPixelRatio(i),t.shadowMap.enabled=e>=1,t.shadowMap.type=Se.PCFShadowMap;let o=this.sky.sun,a=[512,1024,2048,4096][e];o.shadow.mapSize.x!==a&&(o.shadow.mapSize.set(a,a),o.shadow.map?.dispose(),o.shadow.map=null),o.castShadow=e>=1,this.sky.setShadowSize([50,55,70,90][e]),this.scene.fog.far=gi[e],this.scene.fog.near=gi[e]*.3,this.camera.far=6e3,this.camera.updateProjectionMatrix(),t.shadowMap.needsUpdate=!0,this.composer?.dispose();let[n,r]=this.size(),l=new Se.WebGLRenderTarget(n*i,r*i,{type:Se.HalfFloatType,samples:e>=2?4:0}),h=new kt(t,l);if(h.addPass(new zt(this.scene,this.camera)),e>=3){let u=new Ne(this.scene,this.camera,n,r);u.output=Ne.OUTPUT.Default,u.blendIntensity=.85,u.updateGtaoMaterial({radius:.6,distanceExponent:1.5,thickness:1,scale:1,samples:12}),h.addPass(u)}e>=1?(this.bloom=new Ie(new Se.Vector2(n/2,r/2),.25,.4,.93),h.addPass(this.bloom)):this.bloom=null,h.addPass(new Pt),h.addPass(this.grade),e<2&&h.addPass(new Ht),h.setPixelRatio(i),h.setSize(n,r),this.composer=h;for(let u of this.onQualityFns)u(e)}onQuality(e){this.onQualityFns.push(e),this.quality>=0&&e(this.quality)}resize(){let[e,t]=this.size();this.renderer.setSize(e,t),this.composer?.setSize(e,t),this.camera.aspect=e/t,this.camera.updateProjectionMatrix()}set hurt(e){this.grade.uniforms.uHurt.value=e}render(e){this.grade.uniforms.uTime.value+=e,this.bloom&&(this.bloom.strength=.14+.3*this.sky.night),this.beforeRender?.(e),this.composer?.render(e)}};import*as H from"three";var _o=`
attribute float aSize; attribute vec4 aCol; varying vec4 vCol;
void main(){ vCol = aCol; vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_PointSize = aSize * (600.0 / -mv.z); gl_Position = projectionMatrix * mv; }`,Lo=`
uniform sampler2D map; varying vec4 vCol;
void main(){ vec4 t = texture2D(map, gl_PointCoord); gl_FragColor = vec4(vCol.rgb, vCol.a * t.a); }`,It=class{constructor(e,t,s){this.max=t,this.n=0,this.pos=new Float32Array(t*3),this.vel=new Float32Array(t*3),this.col=new Float32Array(t*4),this.size=new Float32Array(t),this.life=new Float32Array(t),this.maxLife=new Float32Array(t),this.grow=new Float32Array(t),this.base=new Float32Array(t),this.rgb=new Float32Array(t*3),this.alpha=new Float32Array(t);let i=new H.BufferGeometry;i.setAttribute("position",new H.BufferAttribute(this.pos,3).setUsage(H.DynamicDrawUsage)),i.setAttribute("aCol",new H.BufferAttribute(this.col,4).setUsage(H.DynamicDrawUsage)),i.setAttribute("aSize",new H.BufferAttribute(this.size,1).setUsage(H.DynamicDrawUsage)),this.geo=i;let o=new H.ShaderMaterial({uniforms:{map:{value:Ms()}},vertexShader:_o,fragmentShader:Lo,transparent:!0,depthWrite:!1,blending:s?H.AdditiveBlending:H.NormalBlending,fog:!1});this.points=new H.Points(i,o),this.points.frustumCulled=!1,this.points.renderOrder=s?3:2,e.add(this.points)}add(e,t,s,i,o,a,n){let r=this.n<this.max?this.n++:Math.floor(Math.random()*this.max);this.pos.set(e,r*3),this.vel.set(t,r*3),this.life[r]=this.maxLife[r]=s,this.base[r]=i,this.grow[r]=o,this.rgb.set(a,r*3),this.alpha[r]=n}update(e,t){for(let s=0;s<this.n;s++){if(this.life[s]-=e,this.life[s]<=0){let r=--this.n;if(s!==r){for(let[l,h]of[[this.pos,3],[this.vel,3],[this.rgb,3]])l.copyWithin(s*h,r*h,r*h+h);for(let l of[this.life,this.maxLife,this.base,this.grow,this.alpha])l[s]=l[r];s--}continue}let i=s*3,o=1-this.life[s]/this.maxLife[s];this.vel[i+1]+=t*e;let a=Math.exp(-1.5*e);this.vel[i]*=a,this.vel[i+2]*=a,this.vel[i+1]*=a,this.pos[i]+=this.vel[i]*e,this.pos[i+1]+=this.vel[i+1]*e,this.pos[i+2]+=this.vel[i+2]*e,this.size[s]=this.base[s]*(1+this.grow[s]*o);let n=this.alpha[s]*(o<.1?o/.1:1-(o-.1)/.9);this.col.set([this.rgb[i],this.rgb[i+1],this.rgb[i+2],n],s*4)}this.geo.setDrawRange(0,this.n);for(let s of["position","aCol","aSize"])this.geo.attributes[s].needsUpdate=!0}},dt=class{constructor(e,t,s,i,o=.9){this.max=t,this.i=0;let a=new H.MeshStandardMaterial({map:s,color:i,transparent:!0,opacity:o,depthWrite:!1,polygonOffset:!0,polygonOffsetFactor:-4,roughness:.9});this.mesh=new H.InstancedMesh(new H.PlaneGeometry(1,1),a,t),this.mesh.count=0,this.mesh.frustumCulled=!1,this.mesh.receiveShadow=!0,e.add(this.mesh),this.m=new H.Matrix4,this.q=new H.Quaternion,this.v=new H.Vector3,this.s=new H.Vector3}add(e,t,s){this.q.setFromUnitVectors(new H.Vector3(0,0,1),this.v.set(t[0],t[1],t[2]).normalize()),this.q.multiply(new H.Quaternion().setFromAxisAngle(new H.Vector3(0,0,1),Math.random()*6.28)),this.m.compose(new H.Vector3(e[0]+t[0]*.015,e[1]+t[1]*.015,e[2]+t[2]*.015),this.q,this.s.set(s,s,s)),this.mesh.setMatrixAt(this.i%this.max,this.m),this.i++,this.mesh.count=Math.min(this.i,this.max),this.mesh.instanceMatrix.needsUpdate=!0}},Nt=class{constructor(e,t){this.g=e,this.three=e.three.scene,this.smoke=new It(this.three,1400,!1),this.fire=new It(this.three,900,!0);let s=le(64,64,(o,a,n)=>{let r=o.createRadialGradient(a/2,n/2,1,a/2,n/2,a/2);r.addColorStop(0,"rgba(10,10,10,1)"),r.addColorStop(.25,"rgba(20,18,16,0.95)"),r.addColorStop(.5,"rgba(60,55,50,0.4)"),r.addColorStop(1,"rgba(0,0,0,0)"),o.fillStyle=r,o.fillRect(0,0,a,n)});this.holes=new dt(this.three,300,s,16777215),this.scorch=new dt(this.three,40,s,2236962,.85),this.blood=new dt(this.three,80,Ms(),4850435,.8),this.lights=[];for(let o=0;o<4;o++){let a=new H.PointLight(16755285,0,18,1.6);a.userData.life=0,a.userData.max=1,a.userData.peak=0,this.three.add(a),this.lights.push(a)}let i=new H.MeshBasicMaterial({map:this.starTexture(),color:new H.Color(3,2.2,1.2),transparent:!0,blending:H.AdditiveBlending,depthWrite:!1,toneMapped:!1,side:H.DoubleSide});this.flashes=[];for(let o=0;o<6;o++){let a=new H.Mesh(new H.PlaneGeometry(.5,.5),i);a.visible=!1,a.userData.t=0,this.three.add(a),this.flashes.push(a)}this.burning=[],this.cam=e.three.camera}starTexture(){return le(64,64,(e,t,s)=>{e.translate(t/2,s/2);let i=e.createRadialGradient(0,0,0,0,0,t/2);i.addColorStop(0,"rgba(255,255,230,1)"),i.addColorStop(.3,"rgba(255,200,90,0.8)"),i.addColorStop(1,"rgba(255,120,20,0)"),e.fillStyle=i;for(let o=0;o<5;o++)e.rotate(Math.PI*2/5),e.beginPath(),e.moveTo(0,-4),e.lineTo(t/2,0),e.lineTo(0,4),e.fill();e.beginPath(),e.arc(0,0,10,0,Math.PI*2),e.fill()})}light(e,t,s,i,o=18){let a=this.lights.reduce((n,r)=>n.userData.life<r.userData.life?n:r);a.position.set(e[0],e[1],e[2]),a.color.set(t),a.distance=o,a.userData.life=a.userData.max=i,a.userData.peak=s,a.intensity=s}muzzle(e,t,s=!1){let i=this.flashes.find(o=>!o.visible)??this.flashes[0];i.visible=!0,i.userData.t=.05,i.position.copy(e),i.lookAt(this.cam.position),i.scale.setScalar(s?1.8:1+Math.random()*.4),i.rotation.z=Math.random()*6.28,this.light([e.x,e.y,e.z],16756832,s?40:14,.06,10)}impact(e,t,s="wall"){let i=this.g.current;if(s==="flesh"){i.burst(e,{count:10,color:["#8a0b0b","#5a0505"],speed:3,life:.35,size:.12,gravity:9,up:.6});return}i.burst(e,{count:7,color:["#ffd27a","#ffffff"],speed:7,life:.18,size:.07,gravity:12,up:.8}),this.smoke.add(e,[t[0]*.8,t[1]*.8+.3,t[2]*.8],.6,.35,2.5,[.55,.52,.48],.45),s==="wall"&&this.holes.add(e,t,.16)}bloodPool(e){this.blood.add([e[0],.02,e[2]],[0,1,0],.8+Math.random()*.6)}explosion(e,t=1){let s=this.g.current;this.light(e,16748608,260*t,.5,30*t);for(let i=0;i<28*t;i++){let o=Math.random()*6.28,a=Math.random();this.fire.add(e,[Math.cos(o)*7*a*t,2+Math.random()*6*t,Math.sin(o)*7*a*t],.45+Math.random()*.4,1.6*t,2.2,[1,.45+Math.random()*.25,.12],.95)}for(let i=0;i<26*t;i++){let o=Math.random()*6.28,a=Math.random();this.smoke.add([e[0],e[1]+.5,e[2]],[Math.cos(o)*4*a*t,1.5+Math.random()*3,Math.sin(o)*4*a*t],2.5+Math.random()*2.5,2.4*t,3,[.07,.065,.06],.85)}s.burst(e,{count:60*t,color:["#ffd27a","#ff7a2a","#ffffff"],speed:22*t,life:.9,size:.12,gravity:14,up:1.2}),s.ring([e[0],e[1],e[2]],{color:"#ffb36b",radius:9*t,life:.45}),this.scorch.add([e[0],.02,e[2]],[0,1,0],4*t)}burn(e,t){this.burning.push({e,until:t})}update(e,t){this.smoke.update(e,1.1),this.fire.update(e,3.5);for(let s of this.lights)s.userData.life>0?(s.userData.life-=e,s.intensity=s.userData.peak*Math.max(0,s.userData.life/s.userData.max)):s.intensity=0;for(let s of this.flashes)s.visible&&(s.userData.t-=e,s.userData.t<=0&&(s.visible=!1));this.burning=this.burning.filter(s=>s.until>t);for(let s of this.burning)if(Math.random()<e*30){let i=s.e,o=[i.x+(Math.random()-.5)*2,i.y+.6,i.z+(Math.random()-.5)*2];this.fire.add(o,[0,1.5,0],.5+Math.random()*.4,.8,1.2,[1,.5,.15],.8),Math.random()<.5&&this.smoke.add([o[0],o[1]+1,o[2]],[.4,2,.2],3,1.2,3.5,[.1,.1,.1],.6)}}};import{sound as Fo}from"kimi/game";var Bt=class{constructor(){this.ctx=null,this.volume=.8,this.listener={x:0,y:0,z:0,yaw:0},this.engines=new Map}start(){if(this.ctx){this.ctx.state==="suspended"&&this.ctx.resume();return}let e=window.AudioContext||window.webkitAudioContext;if(!e)return;this.ctx=new e;let t=this.ctx;this.comp=t.createDynamicsCompressor(),this.comp.threshold.value=-16,this.comp.ratio.value=5,this.master=t.createGain(),this.master.connect(this.comp).connect(t.destination);let s=t.sampleRate*2;this.noise=t.createBuffer(1,s,t.sampleRate);let i=this.noise.getChannelData(0);for(let l=0;l<s;l++)i[l]=Math.random()*2-1;this.brown=t.createBuffer(1,s,t.sampleRate);let o=this.brown.getChannelData(0),a=0;for(let l=0;l<s;l++)a=(a+.02*(Math.random()*2-1))/1.02,o[l]=a*3.5;let n=t.createBufferSource();n.buffer=this.brown,n.loop=!0;let r=t.createBiquadFilter();r.type="lowpass",r.frequency.value=380,this.humGain=t.createGain(),this.humGain.gain.value=.05,n.connect(r).connect(this.humGain).connect(this.master),n.start()}get ready(){return!!this.ctx&&this.ctx.state==="running"}setListener(e,t,s,i){Object.assign(this.listener,{x:e,y:t,z:s,yaw:i})}update(){this.ctx&&this.master.gain.setTargetAtTime(Fo.muted?0:this.volume,this.ctx.currentTime,.05)}place(e,t=120){let s=this.ctx,i=s.createGain(),o=s.createStereoPanner();if(!e)i.gain.value=1,o.pan.value=0;else{let a=e[0]-this.listener.x,n=e[2]-this.listener.z,r=Math.hypot(a,n,(e[1]??0)-this.listener.y);i.gain.value=1/(1+(r/(t*.18))**1.6);let l=Math.atan2(n,a)-this.listener.yaw;o.pan.value=Math.max(-1,Math.min(1,Math.sin(l)*Math.min(1,r/4)))}return i.connect(o).connect(this.master),i}burst(e,t,s,i,o,a="lowpass",n=0,r=this.noise,l=.7){let h=this.ctx,u=h.currentTime+n,d=h.createBufferSource();d.buffer=r;let f=h.createBiquadFilter();f.type=a,f.Q.value=l,f.frequency.setValueAtTime(s,u),f.frequency.exponentialRampToValueAtTime(Math.max(30,i),u+t);let p=h.createGain();p.gain.setValueAtTime(o,u),p.gain.exponentialRampToValueAtTime(1e-4,u+t),d.connect(f).connect(p).connect(e),d.start(u,Math.random()*1.5),d.stop(u+t+.05)}tone(e,t,s,i,o,a="sine",n=0){let r=this.ctx,l=r.currentTime+n,h=r.createOscillator();h.type=a,h.frequency.setValueAtTime(t,l),h.frequency.exponentialRampToValueAtTime(Math.max(20,s),l+i);let u=r.createGain();u.gain.setValueAtTime(1e-4,l),u.gain.exponentialRampToValueAtTime(o,l+.005),u.gain.exponentialRampToValueAtTime(1e-4,l+i),h.connect(u).connect(e),h.start(l),h.stop(l+i+.05)}shot(e,t){if(!this.ready)return;let s=this.place(t,e==="sniper"?400:200),i={pistol:[.16,.5,2600],rifle:[.2,.55,2200],shotgun:[.38,.9,1500],sniper:[.55,1,1800],rocket:[.6,.6,900]}[e]??[.2,.5,2e3],[o,a,n]=i;this.burst(s,o*.35,n*2.2,n*.6,a,"bandpass",0,this.noise,.9),this.burst(s,o,n*.4,60,a*.9,"lowpass"),this.tone(s,e==="sniper"?90:130,40,o*.8,a*.7),e!=="rocket"&&t&&this.burst(s,o*2.5,900,200,a*.12,"lowpass",.08)}explosion(e,t=1){if(!this.ready)return;let s=this.place(e,400);this.burst(s,1.6*t,1800,40,1,"lowpass",0,this.brown),this.burst(s,.5,4e3,300,.6,"lowpass"),this.tone(s,70,25,1.4,.9)}impact(e,t="wall"){if(!this.ready)return;let s=this.place(e,60);t==="flesh"?this.burst(s,.08,900,200,.4,"lowpass"):t==="metal"?(this.tone(s,1800+Math.random()*900,900,.18,.15,"triangle"),this.burst(s,.05,6e3,2e3,.2,"highpass")):this.burst(s,.07,3e3,600,.3,"bandpass"),Math.random()<.15&&t!=="flesh"&&this.tone(s,2400,900,.25,.05,"sine",.02)}step(e,t){if(!this.ready)return;let s=this.place(e,25);this.burst(s,.06,t?1400:900,200,t?.16:.1,"lowpass")}click(){this.ready&&this.tone(this.place(null),2200,1800,.03,.08,"square")}reload(e){if(!this.ready)return;let t=this.place(null);this.tone(t,900,700,.05,.12,"square",.05),this.burst(t,.08,2500,900,.25,"bandpass",.25),this.tone(t,1300,1100,.04,.12,"square",e==="shotgun"?.4:.6)}pickup(){if(this.ready){let e=this.place(null);this.tone(e,880,880,.08,.12,"triangle"),this.tone(e,1320,1320,.12,.12,"triangle",.07)}}hit(){this.ready&&this.tone(this.place(null),1400,1200,.04,.12,"triangle")}thrown(e){this.ready&&this.burst(this.place(e,30),.15,600,2e3,.15,"bandpass")}engine(e,t,s,i,o=!0){if(!this.ready)return;let a=this.engines.get(e);if(!o){a&&(a.g.gain.setTargetAtTime(0,this.ctx.currentTime,.1),setTimeout(()=>{try{a.o.stop(),a.o2.stop(),a.n.stop()}catch{}},400),this.engines.delete(e));return}let n=this.ctx;if(!a){let v=n.createOscillator();v.type="sawtooth";let m=n.createOscillator();m.type="square";let x=n.createBufferSource();x.buffer=this.brown,x.loop=!0;let b=n.createBiquadFilter();b.type="lowpass",b.Q.value=2;let w=n.createGain();w.gain.value=0;let M=n.createGain();M.gain.value=.35;let E=n.createStereoPanner();v.connect(b),m.connect(M).connect(b),x.connect(b),b.connect(w).connect(E).connect(this.master),v.start(),m.start(),x.start(),a={o:v,o2:m,n:x,f:b,g:w,pan:E},this.engines.set(e,a)}let r=t==="boda",l=r?55:32,u=l+((r?210:120)-l)*i,d=n.currentTime;a.o.frequency.setTargetAtTime(u,d,.05),a.o2.frequency.setTargetAtTime(u*(r?2.01:.5),d,.05),a.f.frequency.setTargetAtTime(300+i*(r?2600:1400),d,.05);let f=s[0]-this.listener.x,p=s[2]-this.listener.z,g=Math.hypot(f,p);a.g.gain.setTargetAtTime((.05+i*.1)/(1+(g/18)**1.5),d,.05),a.pan.pan.setTargetAtTime(Math.max(-1,Math.min(1,Math.sin(Math.atan2(p,f)-this.listener.yaw)*Math.min(1,g/4))),d,.1)}stopEngines(){for(let e of[...this.engines.keys()])this.engine(e,"",[0,0,0],0,!1)}siren(e,t){if(!this.ready)return;let s=this.ctx;if(e&&!this.sir){let i=s.createOscillator();i.type="triangle";let o=s.createOscillator();o.frequency.value=.35;let a=s.createGain();a.gain.value=350,o.connect(a).connect(i.frequency),i.frequency.value=950;let n=s.createGain();n.gain.value=0,i.connect(n).connect(this.master),i.start(),o.start(),this.sir={o:i,lfo:o,g:n}}if(this.sir){let i=t?Math.hypot(t[0]-this.listener.x,t[2]-this.listener.z):999;if(this.sir.g.gain.setTargetAtTime(e?.07/(1+(i/40)**1.5):0,s.currentTime,.3),!e&&this.sir.g.gain.value<.001){try{this.sir.o.stop(),this.sir.lfo.stop()}catch{}this.sir=null}}}set heat(e){this.humGain&&(this.humGain.gain.value=.05+e*.01)}};import*as O from"three";var Y={pistol:{id:"pistol",name:"Pistol",model:"pistol",slot:1,dmg:26,rpm:330,auto:!1,mag:12,reserve:72,spread:.014,aimSpread:.004,range:80,kick:.018,reload:1.15,headMul:2.2},rifle:{id:"rifle",name:"Nyundo AR",model:"rifle",slot:2,dmg:21,rpm:640,auto:!0,mag:30,reserve:210,spread:.022,aimSpread:.006,range:150,kick:.011,reload:1.9,headMul:2.2},shotgun:{id:"shotgun",name:"Pump shotgun",model:"shotgun",slot:3,dmg:14,pellets:9,rpm:72,auto:!1,mag:6,reserve:42,spread:.075,aimSpread:.055,range:38,kick:.06,reload:2.3,headMul:1.6},sniper:{id:"sniper",name:"Mbali sniper",model:"sniper",slot:4,dmg:115,rpm:42,auto:!1,mag:5,reserve:30,spread:.05,aimSpread:3e-4,range:600,kick:.07,reload:2.6,scope:7,headMul:3},rocket:{id:"rocket",name:"Rocket launcher",model:"rocket",slot:5,rpm:40,auto:!1,mag:1,reserve:6,projectile:"rocket",speed:58,splash:7.5,dmg:240,reload:2.6,kick:.05},grenade:{id:"grenade",name:"Grenades",model:"grenade",slot:6,rpm:70,auto:!1,mag:1,reserve:5,projectile:"grenade",splash:7.5,dmg:190,reload:.6}},Ce=["pistol","rifle","shotgun","sniper","rocket","grenade"],Ut=["wall","building","prop"],Io=new O.Vector3,Gt=class{constructor(e){this.ctx=e,this.projectiles=[];let t=new O.MeshStandardMaterial({color:3816755,roughness:.5,metalness:.4}),s=new O.MeshBasicMaterial({color:new O.Color(4,2.2,.8),toneMapped:!1});this.makeRocket=()=>{let i=new O.Group,o=new O.Mesh(new O.CylinderGeometry(.06,.06,.7,8).rotateZ(Math.PI/2),t),a=new O.Mesh(new O.ConeGeometry(.06,.2,8).rotateZ(-Math.PI/2),t);a.position.x=.45;let n=new O.Mesh(new O.SphereGeometry(.09,8,6),s);return n.position.x=-.4,i.add(o,a,n),i},this.makeGrenade=()=>new O.Mesh(new O.SphereGeometry(.07,10,8),new O.MeshStandardMaterial({color:4016682,roughness:.6}))}clear(e,t,s){let i=this.ctx.g.current,o=[t[0]-e[0],t[1]-e[1],t[2]-e[2]],a=Math.hypot(...o);return!i.ray3(e,o,a,Ut,s)}fire(e,t,s,i,o,a={}){let{g:n,fx:r,audio:l,vehicles:h}=this.ctx,u=n.current,d=t.pellets??1,f=a.spread??t.spread,p=null;l.shot(t.id,[o.x,o.y,o.z]),r.muzzle(o,i,t.id==="shotgun"||t.id==="sniper"),this.ctx.noise([o.x,o.y,o.z],t.id==="sniper"?90:60,e);let g=[e,...a.ignore??[]];for(let v=0;v<d;v++){let m=No(i,f),x=[s.x,s.y,s.z],b=[m.x,m.y,m.z],w=u.ray3(x,b,t.range,[...Ut,"person"],g),M=h.ray(x,b,w?w.dist:t.range,a.ignoreVehicle);if(M&&(!w||M.dist<w.dist)&&(w=M),m.y<-1e-4){let S=-s.y/m.y;S<(w?w.dist:t.range)&&(w={ground:!0,dist:S,point:[s.x+m.x*S,.01,s.z+m.z*S],normal:[0,1,0]})}let E=w?w.point:[s.x+m.x*t.range,s.y+m.y*t.range,s.z+m.z*t.range];if((v===0||Math.random()<.3)&&(t.id!=="shotgun"||v<3)&&u.beam([o.x,o.y,o.z],E,{color:t.id==="sniper"?"#bfe3ff":"#ffcf7a",width:t.id==="sniper"?.03:.018,life:t.id==="sniper"?.25:.05}),w){if(w.vehicle)w.vehicle.damage(t.dmg*.5*(a.dmgScale??1),e),r.impact(w.point,w.normal,"metal"),l.impact(w.point,"metal");else if(w.entity?.has("person")){let S=w.part==="head";this.hurt(w.entity,t.dmg*(a.dmgScale??1)*(S?t.headMul??2:1),e,{head:S,dir:[m.x,m.y,m.z],weapon:t.id}),r.impact(w.point,w.normal,"flesh"),l.impact(w.point,"flesh")}else r.impact(w.point,w.normal,w.ground?"ground":"wall"),v<2&&l.impact(w.point,"wall");this.ctx.whiz?.(x,b,w.dist,e)}p||(p=w)}return p}hurt(e,t,s,i={}){if(!e.alive||e.invuln>0)return;let o=e.data;if(o.godMode)return;let a=t;if(o.armor>0){let n=Math.min(o.armor,a*.6);o.armor-=n,a-=n}if(o.lastHitBy=s,o.lastHitInfo=i,o.manualHp){e.hp-=a,o.onHurt?.(a,s,i),e.hp<=0&&o.onDeath?.(s,i);return}e.damage(a,s),e.flashT=0,o.onHurt?.(a,s,i),e.alive||this.ctx.onKill?.(e,s,i)}launch(e,t,s,i){let{g:o,audio:a}=this.ctx,n=o.current,r=t.projectile==="rocket",l=r?t.speed:17,h=[i.x*l,i.y*l+(r?0:4.5),i.z*l],u=r?this.makeRocket():this.makeGrenade(),d=n.add({at:[s.x,s.y,s.z],vel:h,radius:.1,mesh:u,tags:["projectile"],dies:r?6:10,face:"none"});return d.rot=Math.atan2(i.z,i.x),d.data={owner:e,def:t,fuse:r?99:2.4,rocket:r},r||a.thrown([s.x,s.y,s.z]),this.projectiles.push(d),d}update(e){let{g:t,fx:s,vehicles:i}=this.ctx,o=t.current;for(let a of this.projectiles){if(!a.alive)continue;let n=a.data;if(n.rocket){let r=[a.ox,a.oy,a.oz],l=[a.x-a.ox,a.y-a.oy,a.z-a.oz],h=Math.hypot(...l)||.001,u=o.ray3(r,l,h,[...Ut,"person"],[n.owner]),d=i.ray(r,l,u?u.dist:h,n.owner?.data?.vehicle);d&&(!u||d.dist<u.dist)&&(u=d);let f=this.ctx.world.groundAt(a.x,a.z,a.y+.5);a.y<=f+.05&&(u={point:[a.x,f+.05,a.z]}),Math.random()<.9&&s.smoke.add([a.x,a.y,a.z],[0,.2,0],1.2,.35,2.5,[.7,.68,.65],.4),u&&(a.die("hit"),this.explode(u.point,n.def,n.owner))}else{a.vy-=20*e;let r=this.ctx.world.groundAt(a.x,a.z,a.y);a.y<r+.07&&a.vy<0&&(a.y=r+.07,a.vy*=-.35,a.vx*=.6,a.vz*=.6);let l=[a.x-a.ox,a.y-a.oy,a.z-a.oz],h=Math.hypot(...l);if(h>.001){let u=o.ray3([a.ox,a.oy,a.oz],l,h+.08,Ut);if(u){let d=u.normal,f=a.vx*d[0]+a.vy*d[1]+a.vz*d[2];a.vx-=1.6*f*d[0],a.vy-=1.6*f*d[1],a.vz-=1.6*f*d[2],a.x=a.ox,a.y=a.oy,a.z=a.oz}}n.fuse-=e,n.fuse<=0&&(a.die("fuse"),this.explode([a.x,a.y+.2,a.z],n.def,n.owner))}}this.projectiles=this.projectiles.filter(a=>a.alive)}explode(e,t,s){let{g:i,fx:o,audio:a,vehicles:n}=this.ctx,r=i.current;o.explosion(e,t.id==="rocket"?1:.8),a.explosion(e,1),this.ctx.noise(e,140,s),this.ctx.shake?.(e,1.2);let l=t.splash;for(let h of r.within("person",[e[0],e[2]],l)){let u=Math.hypot(h.x-e[0],h.y-e[1],h.z-e[2]);if(u>l)continue;let d=1-u/l;this.clear(e,[h.x,h.y+.4,h.z],[h])&&(this.hurt(h,t.dmg*d*d+15*d,s,{blast:!0,dir:[h.x-e[0],.5,h.z-e[2]]}),h.data.knock=[(h.x-e[0])*d*4,5*d,(h.z-e[2])*d*4])}n.blast(e,l*1.3,t.dmg,s);for(let h of this.projectiles)h.alive&&Math.hypot(h.x-e[0],h.z-e[2])<3&&(h.data.fuse=Math.min(h.data.fuse,.05))}};function No(c,e){if(!e)return c.clone();let t=Math.random()*Math.PI*2,s=Math.sqrt(Math.random())*e,i=Math.abs(c.y)<.9?new O.Vector3(0,1,0):new O.Vector3(1,0,0),o=Io.crossVectors(c,i).normalize(),a=new O.Vector3().crossVectors(c,o).normalize();return c.clone().addScaledVector(o,Math.cos(t)*s).addScaledVector(a,Math.sin(t)*s).normalize()}import*as I from"three";import*as C from"three";var ft=["hips","spine","chest","neck","head","shoulderL","upperArmL","foreArmL","handL","shoulderR","upperArmR","foreArmR","handR","thighL","shinL","footL","thighR","shinR","footR"],mt=Object.fromEntries(ft.map((c,e)=>[c,e])),Ue={hips:[0,.95,0],spine:[0,1.05,0],chest:[0,1.22,0],neck:[0,1.52,0],head:[0,1.62,0],shoulderL:[0,1.46,-.17],upperArmL:[0,1.46,-.23],foreArmL:[0,1.19,-.25],handL:[0,.94,-.26],shoulderR:[0,1.46,.17],upperArmR:[0,1.46,.23],foreArmR:[0,1.19,.25],handR:[0,.94,.26],thighL:[0,.93,-.1],shinL:[0,.5,-.1],footL:[0,.08,-.1],thighR:[0,.93,.1],shinR:[0,.5,.1],footR:[0,.08,.1]},Ot={spine:"hips",chest:"spine",neck:"chest",head:"neck",shoulderL:"chest",upperArmL:"shoulderL",foreArmL:"upperArmL",handL:"foreArmL",shoulderR:"chest",upperArmR:"shoulderR",foreArmR:"upperArmR",handR:"foreArmR",thighL:"hips",shinL:"thighL",footL:"shinL",thighR:"hips",shinR:"thighR",footR:"shinR"},ne={player:{skin:7029808,shirt:2846607,sleeve:"short",pants:2764602,shoes:1710618,hat:"cap",hatColor:2042422,vest:3951146},gang:{skin:5913128,shirt:1908002,sleeve:"long",pants:2829104,shoes:1118481,hat:"bandana",hatColor:11740702,vest:null},gangBoss:{skin:4861728,shirt:5902352,sleeve:"long",pants:1447446,shoes:855309,hat:"beret",hatColor:1118481,vest:2829099},police:{skin:7227955,shirt:2375790,sleeve:"short",pants:1450813,shoes:855309,hat:"police",hatColor:1450813,vest:3027512},civilian:{skin:8015928,shirt:12623968,sleeve:"short",pants:3820138,shoes:3811866,hat:null,hatColor:0,vest:null}},Bo=[4074014,5190436,7029808,8015928,9264963,11040086,13014137],As=[12597547,2719929,2600544,15965202,9323693,15528177,3426654,13849600,1482885,15259056,8359053,16740241],Uo=[2899536,3426654,6111287,1779249,7171437,9141611];function Wt(c){return{skin:c.pick(Bo),shirt:c.pick(As),sleeve:c.chance(.6)?"short":"long",pants:c.pick(Uo),shoes:c.pick([1710618,3811866,14606046]),hat:c.chance(.25)?c.pick(["cap","kofia"]):null,hatColor:c.pick(As),vest:null,dress:c.chance(.28)?c.pick(As):null}}function qt(c,e){let t=new C.Color(e),s=c.attributes.position.count,i=new Float32Array(s*3);for(let o=0;o<s;o++)i[o*3]=t.r,i[o*3+1]=t.g,i[o*3+2]=t.b;return c.setAttribute("color",new C.BufferAttribute(i,3)),c}function Yt(c,e){let t=c.attributes.position.count,s=new Uint16Array(t*4),i=new Float32Array(t*4);for(let o=0;o<t;o++)s[o*4]=mt[e],i[o*4]=1;return c.setAttribute("skinIndex",new C.BufferAttribute(s,4)),c.setAttribute("skinWeight",new C.BufferAttribute(i,4)),c}var Kt=c=>(c.index&&(c=c.toNonIndexed()),c.deleteAttribute("uv"),c);function pt(c,e,t,s,i){let o=Math.hypot(e[0]-c[0],e[1]-c[1],e[2]-c[2]),a=new C.CapsuleGeometry(t,Math.max(.01,o-t),3,8),n=new C.Vector3(e[0]-c[0],e[1]-c[1],e[2]-c[2]).normalize();return a.applyQuaternion(new C.Quaternion().setFromUnitVectors(new C.Vector3(0,1,0),n)),a.translate((c[0]+e[0])/2,(c[1]+e[1])/2,(c[2]+e[2])/2),Yt(qt(Kt(a),s),i)}function ue(c,e,t,s,i,o,a,n,r=.04){let l=new fe(c,e,t,2,Math.min(r,c/2,e/2,t/2));return l.translate(s,i,o),Yt(qt(Kt(l),a),n)}function Be(c,e,t,s,i,o,a=1,n=1,r=1){let l=new C.SphereGeometry(c,14,10);return l.scale(a,n,r),l.translate(e,t,s),Yt(qt(Kt(l),i),o)}var Ps=new Map;function Go(c){let e=JSON.stringify(c);if(Ps.has(e))return Ps.get(e);let t=Ue,s=[],i=c.sleeve==="long"?c.shirt:c.skin;for(let a of["L","R"])s.push(pt(t["thigh"+a],t["shin"+a],.078,c.dress??c.pants,"thigh"+a)),s.push(pt(t["shin"+a],t["foot"+a],.062,c.dress?c.skin:c.pants,"shin"+a)),s.push(ue(.25,.08,.11,t["foot"+a][0]+.06,.04,t["foot"+a][2],c.shoes,"foot"+a,.03)),s.push(pt(t["upperArm"+a],t["foreArm"+a],.058,c.shirt,"upperArm"+a)),s.push(pt(t["foreArm"+a],t["hand"+a],.05,i,"foreArm"+a)),s.push(ue(.08,.11,.06,0,t["hand"+a][1]-.05,t["hand"+a][2],c.skin,"hand"+a,.025));if(s.push(ue(.24,.22,.36,0,.97,0,c.dress??c.pants,"hips",.07)),s.push(ue(.24,.24,.38,0,1.17,0,c.shirt,"spine",.08)),s.push(ue(.26,.3,.44,0,1.38,0,c.shirt,"chest",.1)),c.dress&&s.push(ue(.34,.3,.42,0,.82,0,c.dress,"hips",.1)),c.vest&&s.push(ue(.3,.42,.42,0,1.31,0,c.vest,"chest",.06)),s.push(pt([0,1.52,0],[0,1.62,0],.055,c.skin,"neck")),s.push(Be(.115,.01,1.73,0,c.skin,"head",1,1.15,.95)),s.push(ue(.04,.05,.04,.115,1.72,0,c.skin,"head",.015)),c.hat==="cap"||c.hat==="police")s.push(Be(.122,0,1.79,0,c.hatColor,"head",1.05,.7,1)),s.push(ue(.12,.02,.2,.13,1.8,0,c.hatColor,"head",.01)),c.hat==="police"&&s.push(ue(.03,.05,.08,.1,1.84,0,13938487,"head",.01));else if(c.hat==="bandana")s.push(Be(.121,0,1.78,0,c.hatColor,"head",1.04,.72,1));else if(c.hat==="beret")s.push(Be(.13,-.02,1.83,.02,c.hatColor,"head",1.1,.45,1.05));else if(c.hat==="kofia"){let a=new C.CylinderGeometry(.115,.118,.1,14);a.translate(0,1.82,0),s.push(Yt(qt(Kt(a),c.hatColor),"head"))}else c.hat==="helmet"?s.push(Be(.15,0,1.75,0,c.hatColor,"head",1.05,1,1)):s.push(Be(.118,-.01,1.77,0,1380624,"head",1,.85,.96));let o=Fe(s);return o.computeBoundingSphere(),Ps.set(e,o),o}var Oo=new C.MeshStandardMaterial({vertexColors:!0,roughness:.78,metalness:.02}),de=new C.MeshStandardMaterial({color:1842464,roughness:.35,metalness:.85}),Vt=new C.MeshStandardMaterial({color:2828838,roughness:.7,metalness:.1}),jt=new C.MeshStandardMaterial({color:5912608,roughness:.6}),Hs=new C.MeshStandardMaterial({color:7037511,roughness:.6,metalness:.2}),Vo=new C.MeshStandardMaterial({color:662058,roughness:.05,metalness:1,emissive:664640,emissiveIntensity:.4});function j(c,e,t,s,i,o=0){let a=new C.Mesh(c,e);return a.position.set(t,s,i),a.rotation.z=o,a.castShadow=!0,a}var J=(c,e,t)=>new C.BoxGeometry(c,e,t),De=(c,e)=>{let t=new C.CylinderGeometry(c,c,e,10);return t.rotateZ(Math.PI/2),t};function Cs(c){let e=new C.Group,t=new C.Object3D;return t.name="muzzle",c==="pistol"?(e.add(j(J(.2,.05,.035),de,.06,.03,0),j(J(.05,.11,.035),Vt,-.01,-.03,0,.25)),t.position.set(.17,.035,0)):c==="rifle"?(e.add(j(J(.42,.07,.05),de,.12,.02,0),j(De(.015,.3),de,.47,.035,0),j(J(.22,.08,.045),jt,-.2,0,0),j(J(.05,.16,.04),de,.12,-.08,0,-.35),j(J(.05,.1,.04),Vt,-.02,-.06,0,.3),j(J(.16,.05,.05),jt,.3,0,0)),t.position.set(.63,.035,0)):c==="shotgun"?(e.add(j(De(.022,.6),de,.28,.035,0),j(De(.02,.45),de,.22,0,0),j(J(.26,.08,.05),jt,-.2,0,0),j(J(.14,.05,.05),jt,.2,-.01,0)),t.position.set(.59,.035,0)):c==="sniper"?(e.add(j(J(.4,.06,.05),Hs,.05,0,0),j(De(.016,.55),de,.52,.02,0),j(J(.3,.09,.045),Hs,-.3,-.01,0),j(De(.032,.3),de,.08,.09,0),j(new C.CircleGeometry(.028,12).rotateY(Math.PI/2),Vo,.235,.09,0),j(J(.05,.13,.04),Vt,-.06,-.07,0,.3)),t.position.set(.8,.02,0)):c==="rocket"?(e.add(j(De(.075,1),Hs,.15,.06,0),j(De(.085,.12),de,.65,.06,0),j(J(.05,.14,.04),Vt,.05,-.05,0,.2),j(J(.12,.06,.05),de,.2,.15,0)),t.position.set(.72,.06,0)):c==="grenade"&&(e.add(j(new C.SphereGeometry(.05,10,8),new C.MeshStandardMaterial({color:4016682,roughness:.6}),0,0,0)),t.position.set(.05,0,0)),e.add(t),e.userData.muzzle=t,e}var ie=class{constructor(e){this.look=e,this.group=new C.Group;let t=ft.map(i=>{let o=new C.Bone;return o.name=i,o});for(let i of ft){let o=t[mt[i]],a=Ue[i],n=Ot[i]?Ue[Ot[i]]:[0,0,0];o.position.set(a[0]-n[0],a[1]-n[1],a[2]-n[2]),Ot[i]&&t[mt[Ot[i]]].add(o)}this.bones=Object.fromEntries(ft.map(i=>[i,t[mt[i]]]));let s=new C.SkinnedMesh(Go(e),Oo);s.add(t[mt.hips]),s.bind(new C.Skeleton(t)),s.castShadow=!0,s.receiveShadow=!0,s.frustumCulled=!1,s.position.y=-.9,this.mesh=s,this.body=new C.Group,this.body.add(s),this.group.add(this.body),this.phase=Math.random()*10,this.weapon=null,this.weaponKind=null,this.recoil=0,this.flinch=0,this.dead=0,this.deadDir=1,this.crouch=0,this.aimBlend=0}setWeapon(e){this.weaponKind!==e&&(this.weapon&&this.weapon.removeFromParent(),this.weaponKind=e,this.weapon=e?Cs(e):null,this.weapon&&(this.weapon.rotation.set(0,0,-Math.PI/2),this.weapon.position.set(0,-.09,.02),this.bones.handR.add(this.weapon)))}muzzleWorld(e=new C.Vector3){return this.weapon?(this.group.updateWorldMatrix(!0,!0),this.weapon.userData.muzzle.getWorldPosition(e)):this.bones.handR.getWorldPosition(e)}animate(e,t){let s=this.bones,i=(M,E,S)=>M+(E-M)*Math.min(1,S);this.aimBlend=i(this.aimBlend,t.aim?1:0,e*12),this.crouch=i(this.crouch,t.crouch?1:0,e*10),this.recoil=Math.max(0,this.recoil-e*9),this.flinch=Math.max(0,this.flinch-e*4);for(let M of ft)s[M].rotation.set(0,0,0);if(s.hips.position.set(0,Ue.hips[1],0),t.dead){this.dead=Math.min(1,this.dead+e*2.4);let M=1-Math.pow(1-this.dead,3);this.body.rotation.z=-this.deadDir*M*1.5,this.body.position.y=-M*.75,s.thighL.rotation.z=.3*M,s.thighR.rotation.z=-.2*M,s.upperArmL.rotation.x=-1.2*M,s.upperArmR.rotation.x=1.2*M;return}if(this.dead=0,this.body.rotation.set(0,0,0),this.body.position.set(0,0,0),t.ride){s.hips.position.y=Ue.hips[1]-(t.ride==="bike"?.05:.38),s.thighL.rotation.z=s.thighR.rotation.z=1.45,s.shinL.rotation.z=s.shinR.rotation.z=t.ride==="bike"?-1.75:-1.45;let M=t.ride==="bike"?.35:.1;s.spine.rotation.z=-M,s.upperArmL.rotation.z=s.upperArmR.rotation.z=1+M,s.foreArmL.rotation.z=s.foreArmR.rotation.z=.4,s.upperArmL.rotation.x=.15,s.upperArmR.rotation.x=-.15,t.aim&&(s.upperArmR.rotation.z=1.5,s.foreArmR.rotation.z=0,s.chest.rotation.z=t.pitch*.6);return}let o=t.speed,a=t.sprint?2.1:1.45;this.phase+=o/a*Math.PI*e;let n=this.phase,r=Math.min(1,o/2.2)*(t.sprint?.95:.62),l=t.fwd<-.3?-1:1,h=Math.abs(t.side)>.5&&Math.abs(t.fwd)<.5,u=Math.sin(n)*r*l;h?(s.thighL.rotation.x=Math.sin(n)*r*.6*Math.sign(t.side),s.thighR.rotation.x=-Math.sin(n)*r*.6*Math.sign(t.side)):(s.thighL.rotation.z=u,s.thighR.rotation.z=-u),s.shinL.rotation.z=-Math.max(0,-Math.cos(n))*r*1.3,s.shinR.rotation.z=-Math.max(0,Math.cos(n))*r*1.3,s.footL.rotation.z=-u*.3,s.footR.rotation.z=u*.3;let d=Math.abs(Math.cos(n))*r*.06;s.hips.position.y=Ue.hips[1]-d-this.crouch*.32,s.hips.rotation.y=u*.15,s.spine.rotation.z=-(t.sprint?.22:.06)*Math.min(1,o/3)+this.crouch*.3,this.crouch>.01&&(s.thighL.rotation.z+=this.crouch*.95,s.thighR.rotation.z+=this.crouch*.75,s.shinL.rotation.z-=this.crouch*1.5,s.shinR.rotation.z-=this.crouch*1.25,s.footL.rotation.z+=this.crouch*.5,s.footR.rotation.z+=this.crouch*.5);let f={z:-u*.8,x:.12},p={z:u*.8,x:-.12},g=t.pitch??0,v=this.weaponKind&&this.weaponKind!=="pistol"&&this.weaponKind!=="grenade",m=this.weaponKind?Math.max(this.aimBlend,v?.35:0):0,x=this.recoil*(v?.12:.3),b={z:1.5+g*.8+x,x:0,y:.12,fz:0},w=v?{z:1.3+g*.8,x:0,y:-.6,fz:.25}:{z:1.45+g*.8,x:0,y:-.45,fz:.1};s.upperArmR.rotation.z=i(p.z,b.z,m),s.upperArmR.rotation.x=i(p.x,b.x,m),s.upperArmR.rotation.y=i(0,b.y,m),s.foreArmR.rotation.z=i(.15,b.fz,m),s.upperArmL.rotation.z=i(f.z,w.z,m),s.upperArmL.rotation.x=i(f.x,w.x,m),s.upperArmL.rotation.y=i(0,w.y,m),s.foreArmL.rotation.z=i(.15,w.fz,m),s.chest.rotation.z=g*.35*m+this.recoil*.05-this.flinch*.25,s.chest.rotation.x=this.flinch*.2*Math.sin(this.phase*3),s.head.rotation.z=g*.25}};var bi={sedan:{len:4.4,wid:1.9,hgt:1.5,accel:14,top:47,grip:7.5,turn:2,hp:220,brake:30,seatY:.62,seatX:-.25},suv:{len:4.8,wid:2.05,hgt:1.95,accel:12,top:43,grip:7,turn:1.8,hp:300,brake:28,seatY:.85,seatX:-.3},police:{len:4.8,wid:2.05,hgt:1.95,accel:13.5,top:48,grip:7.6,turn:1.9,hp:320,brake:30,seatY:.85,seatX:-.3},armored:{len:5,wid:2.2,hgt:2.1,accel:10,top:40,grip:7,turn:1.6,hp:650,brake:26,seatY:.9,seatX:-.3},matatu:{len:5.6,wid:2.1,hgt:2.45,accel:10,top:38,grip:6.2,turn:1.55,hp:340,brake:24,seatY:.95,seatX:1.6},boda:{len:2,wid:.7,hgt:1.15,accel:17,top:52,grip:9,turn:2.7,hp:110,brake:34,seatY:.62,seatX:-.15,bike:!0}},jo=[12000284,870305,1793568,16361509,15658734,2171169,6953882,33679,12531212,10395294,4073251,1713022],Wo=new I.CylinderGeometry(1,1,1,18).rotateX(Math.PI/2),qo=new I.CylinderGeometry(1,1,1,10).rotateX(Math.PI/2),Ds=new I.MeshPhysicalMaterial({color:791576,roughness:.04,metalness:.3,envMapIntensity:2,clearcoat:1}),Ge=new I.MeshStandardMaterial({color:16777215,emissive:16773840,emissiveIntensity:.2,roughness:.1}),gt=new I.MeshStandardMaterial({color:5570560,emissive:16715792,emissiveIntensity:.4,roughness:.2}),_e=new I.MeshStandardMaterial({color:1381914,roughness:.55,metalness:.3}),vi=new I.MeshStandardMaterial({color:1118481,roughness:1}),Ls=new I.MeshStandardMaterial({color:4194304,emissive:16711712,emissiveIntensity:0}),Fs=new I.MeshStandardMaterial({color:64,emissive:2113791,emissiveIntensity:0}),xi={headMat:Ge,tailMat:gt};function Yo(c){return new I.MeshPhysicalMaterial({color:c,roughness:.32,metalness:.55,clearcoat:1,clearcoatRoughness:.08,envMapIntensity:1.3})}function G(c,e,t,s,i,o,a,n){let r=new I.Mesh(new fe(c,e,t,3,Math.min(s,c/2-.01,e/2-.01,t/2-.01)),i);return r.position.set(o,a,n),r.castShadow=!0,r.receiveShadow=!0,r}function _s(c,e,t,s,i){let o=new I.Group,a=new I.Group,n=new I.Mesh(Wo,L.rubber());n.scale.set(c,c,e),n.castShadow=!0;let r=new I.Mesh(qo,L.metal());return r.scale.set(c*.6,c*.6,e*1.04),a.add(n,r),o.add(a),o.position.set(t,c,s),{g:o,spin:a,front:i,r:c}}function Ko(c,e,t){let s=bi[c],i=new I.Group,o=new I.Group;i.add(o);let a=[],n=(...h)=>o.add(...h),r=e,l=null;if(c==="boda"){n(G(.62,.26,.3,.1,r,.15,.82,0)),n(G(.8,.12,.3,.05,_e,-.35,.86,0)),n(G(.5,.35,.28,.06,L.darkMetal(),.05,.5,0)),n(G(.45,.06,.4,.03,r,-.75,.88,0));let h=new I.Mesh(new I.CylinderGeometry(.03,.03,.75,6),L.metal());h.position.set(.72,.68,0),h.rotation.z=-.35,n(h);let u=new I.Mesh(new I.CylinderGeometry(.025,.025,.7,6).rotateX(Math.PI/2),_e);u.position.set(.6,1.08,0),n(u);let d=new I.Mesh(new I.SphereGeometry(.09,10,8),Ge);d.position.set(.82,.95,0),n(d);let f=new I.Mesh(new I.CylinderGeometry(.04,.05,.7,8).rotateZ(Math.PI/2),L.metal());f.position.set(-.4,.38,.17),n(f);for(let[p,g]of[[.8,!0],[-.72,!1]]){let v=_s(.33,.1,p,0,g);n(v.g),a.push(v)}}else if(c==="matatu"){let h=new I.MeshPhysicalMaterial({map:ei(t),roughness:.35,metalness:.3,clearcoat:1,envMapIntensity:1.2});n(G(5.6,1.75,2.1,.25,h,0,1.3,0)),n(G(4.4,.55,2.14,.12,Ds,-.5,1.75,0)),n(G(.12,.8,1.9,.05,Ds,2.78,1.65,0)),n(G(3.6,.12,1.6,.04,L.darkMetal(),-.4,2.25,0)),n(G(.2,.3,2,.08,_e,2.82,.6,0),G(.2,.3,2,.08,_e,-2.82,.6,0)),n(G(.06,.15,.4,.03,Ge,2.85,.95,.72),G(.06,.15,.4,.03,Ge,2.85,.95,-.72)),n(G(.06,.25,.25,.03,gt,-2.83,1,.85),G(.06,.25,.25,.03,gt,-2.83,1,-.85));for(let[u,d,f]of[[1.85,.95,!0],[1.85,-.95,!0],[-1.85,.95,!1],[-1.85,-.95,!1]]){let p=_s(.4,.28,u,d,f);n(p.g),a.push(p)}}else{let h=c!=="sedan",u=s.len,d=s.wid,f=h?.9:.62,p=h?.88:.7;n(G(u,f,d,.2,r,0,p,0));let g=h?u*.62:u*.5,v=h?-.35:-.25,m=h?.66:.55,x=p+f/2+m/2-.04;if(n(G(g,m,d*.9,.18,Ds,v,x,0)),n(G(g*.92,.09,d*.88,.04,r,v-.05,x+m/2,0)),n(G(.2,.28,d*.96,.08,_e,u/2,p-.2,0),G(.2,.28,d*.96,.08,_e,-u/2,p-.2,0)),n(G(.08,.13,.42,.04,Ge,u/2+.01,p+.12,d*.32),G(.08,.13,.42,.04,Ge,u/2+.01,p+.12,-d*.32)),n(G(.08,.14,.36,.04,gt,-u/2-.01,p+.14,d*.34),G(.08,.14,.36,.04,gt,-u/2-.01,p+.14,-d*.34)),n(G(.05,.22,d*.45,.03,_e,u/2+.02,p-.02,0)),c==="police"){let E=G(u*.7,.18,d+.02,.05,new I.MeshStandardMaterial({color:1720224,roughness:.4}),0,p+.05,0);n(E),l=[G(.3,.12,.55,.04,Ls,v,x+m/2+.1,.32),G(.3,.12,.55,.04,Fs,v,x+m/2+.1,-.32)],n(...l)}c==="armored"&&n(G(u*.5,.06,d+.06,.02,L.darkMetal(),.3,p+.3,0),G(.15,.5,d*.9,.05,L.darkMetal(),u/2+.12,p-.05,0));let b=h?.42:.34,w=u/2-(h?.95:.8),M=d/2-.12;for(let[E,S,T]of[[w,M,!0],[w,-M,!0],[-w,M,!1],[-w,-M,!1]]){let R=_s(b,h?.3:.24,E,S,T);n(R.g),a.push(R)}}return{root:i,body:o,wheels:a,bar:l}}var $o=1,Is=class{constructor(e,t,s,i,o,a={}){this.ctx=e,this.id=$o++,this.type=t,this.T=bi[t],this.paint=Yo(a.paint??(t==="police"?15921906:t==="armored"?1316378:e.g.pick(jo)));let n=Ko(t,this.paint,a.livery??Math.floor(Math.random()*6));this.mesh=n;let r=this.T,l=e.world.groundAt(s,i,0);this.e=e.g.current.add({at:[s,l+r.hgt/2,i],capsule:[r.wid*.55,r.hgt],radius:r.bike?.55:r.wid*.6,mesh:n.root,tags:["vehicle"],solid:["vehicle","person"],mass:r.bike?3:14,face:"none",hp:r.hp,rot:o}),n.root.children[0].position.y=-r.hgt/2,this.e.data.vehicleObj=this,this.e.rot=this.e.orot=o,this.vy=0,this.speed=0,this.steerVis=0,this.driver=null,this.wrecked=!1,this.pitch=0,this.roll=0,this.air=0,this.ai=null,this.lastHit=0,this.e.on("hit","person",h=>this.runOver(h))}get x(){return this.e.x}get z(){return this.e.z}get groundY(){return this.e.y-this.T.hgt/2}get fwd(){return[Math.cos(this.e.rot),Math.sin(this.e.rot)]}seat(e){if(this.driver=e,e){let t=this.T;e.group.position.set(t.seatX,t.seatY+.9-.15,t.bike?0:.38),e.group.rotation.set(0,0,0),this.mesh.body.add(e.group),e.animate(.016,{speed:0,ride:t.bike?"bike":"car"})}}unseat(){let e=this.driver;return e&&(e.group.removeFromParent(),this.driver=null),e}drive(e,t){let s=this.e,i=this.T,o=this.ctx.world;this.wrecked&&(t={throttle:0,steer:0,brake:!0});let[a,n]=this.fwd,r=-n,l=a,h=s.vx*a+s.vz*n,u=s.vx*r+s.vz*l;if(this.air<=0){let M=t.throttle;M>0?h+=i.accel*M*e*Math.max(0,1-h/i.top)*(h<0?2:1):M<0?h>.6?h-=i.brake*-M*e:h=Math.max(-12,h+i.accel*.6*M*e):h-=Math.sign(h)*Math.min(Math.abs(h),2.2*e),h*=Math.exp(-.06*e);let E=t.brake?i.grip*.12:i.grip;u*=Math.exp(-E*e),t.brake&&(h-=Math.sign(h)*Math.min(Math.abs(h),9*e));let S=Math.min(1,Math.abs(h)/7)*Math.sign(h),T=t.steer*i.turn*S*(t.brake?1.45:1)*(1-.4*Math.min(1,Math.abs(h)/i.top));s.rot+=T*e,u+=-T*h*.04*e*(t.brake?6:1)}let[f,p]=this.fwd;s.vx=f*h+-p*u,s.vz=p*h+f*u,this.speed=h,this.steerVis+=(t.steer-this.steerVis)*Math.min(1,e*8);let g=o.groundAt(s.x,s.z,this.groundY+.5,.9),v=this.groundY;if(v>g+.06||this.vy>.5)this.vy-=22*e,s.y+=this.vy*e,this.air=.2,this.groundY<=g&&(s.y=g+i.hgt/2,this.vy<-9&&(this.damage(-this.vy*2.5,null),this.ctx.fx.impact([s.x,g+.1,s.z],[0,1,0],"ground"),this.ctx.audio.impact([s.x,g,s.z],"metal"),this.ctx.shake?.([s.x,g,s.z],.4)),this.vy=0);else{let M=(g-v)/Math.max(e,1e-4);s.y=g+i.hgt/2,this.vy=Math.max(0,Math.min(M,14)),this.air=Math.max(0,this.air-e)}let m=o.groundAt(s.x+f*i.len*.45,s.z+p*i.len*.45,this.groundY+.6,.9),x=o.groundAt(s.x-f*i.len*.45,s.z-p*i.len*.45,this.groundY+.6,.9),b=this.air>0?this.pitch:Math.atan2(m-x,i.len*.9);this.pitch+=(b-this.pitch)*Math.min(1,e*8);let w=i.bike?-t.steer*Math.min(1,Math.abs(h)/12)*.5:t.steer*Math.min(1,Math.abs(h)/20)*.06;this.roll+=(w-this.roll)*Math.min(1,e*6),this.mesh.body.rotation.set(this.roll,0,this.pitch,"YXZ");for(let M of this.mesh.wheels)M.spin.rotation.z-=h/M.r*e,M.front&&(M.g.rotation.y=-this.steerVis*.5);this.collideStatic(e)}collideStatic(e){let t=this.e,s=this.T,i=this.ctx.g.current,[o,a]=this.fwd,n=-a,r=o,l=s.len/2,h=s.wid/2,u=[[l,h],[l,-h],[-l,h],[-l,-h],[l,0],[-l,0],[0,h],[0,-h]],d=this.groundY+.25,f=this.groundY+s.hgt,p=0,g=0,v=0,m=[];for(let[E,S]of u){let T=t.x+o*E+n*S,R=t.z+a*E+r*S;m.length=0;for(let z of i.staticHash.query(T,R,.3,m))if(z.box){let k=z.box[1]/2;if(z.y+k<d||z.y-k>f)continue;let P=T-z.x,_=R-z.z,V=z.box[0]/2,oe=(z.box[2]??z.box[0])/2;if(Math.abs(P)<V&&Math.abs(_)<oe){let ee=V-Math.abs(P),me=oe-Math.abs(_);ee<me?p+=Math.sign(P)*ee:g+=Math.sign(_)*me,v++}}else if(z.opts.capsule){let k=z.radius+.05,P=T-z.x,_=R-z.z,V=Math.hypot(P,_);V<k&&z.y+z.opts.capsule[1]/2>d&&(p+=P/(V||1)*(k-V),g+=_/(V||1)*(k-V),v++)}}if(!v)return;let x=Math.hypot(p,g)||1,b=p/x,w=g/x;t.x+=p/Math.max(1,v*.5),t.z+=g/Math.max(1,v*.5);let M=t.vx*b+t.vz*w;if(M<0){t.vx-=1.3*M*b,t.vz-=1.3*M*w;let E=-M;E>6&&this.ctx.g.time-this.lastHit>.3&&(this.lastHit=this.ctx.g.time,this.damage(E*E*.35,null),this.ctx.audio.impact([t.x,t.y,t.z],"metal"),this.ctx.fx.impact([t.x+b*-1,t.y,t.z+w*-1],[b,0,w],"metal"),this.driver?.isPlayer&&(this.ctx.shake?.([t.x,t.y,t.z],Math.min(1,E/25)),this.ctx.g.input.vibrate(120,Math.min(1,E/20))))}}runOver(e){let t=Math.hypot(this.e.vx,this.e.vz);t<5||e.data.vehicle===this||(this.ctx.combat.hurt(e,t*7,this.driver?.entity??null,{runOver:!0}),e.data.knock=[this.e.vx*.5,4,this.e.vz*.5],this.ctx.audio.impact([e.x,e.y,e.z],"flesh"),this.driver?.isPlayer&&this.ctx.noise([e.x,e.y,e.z],30,this.driver.entity))}damage(e,t){this.wrecked||e<=0||(this.e.hp-=e,this.e.data.lastHitBy=t,this.onDamage?.(e,t),this.e.hp<=this.T.hp*.3&&Math.random()<.3&&(this.smoking=!0),this.e.hp<=0&&this.explode(t))}explode(e){if(this.wrecked)return;this.wrecked=!0;let t=this.e,s=[t.x,t.y,t.z];this.ctx.fx.explosion(s,1.3),this.ctx.audio.explosion(s,1.3),this.ctx.fx.burn(t,this.ctx.g.time+14),this.ctx.noise(s,140,e),this.ctx.shake?.(s,1.4),this.vy=7,this.e.y+=.1,this.mesh.root.traverse(i=>{i.isMesh&&i.material!==L.rubber()&&(i.material=vi)});for(let i of this.mesh.bar??[])i.material=vi;this.ctx.combat.explode(s,{id:"car",splash:6,dmg:120},e),this.onWreck?.(e),this.ctx.onVehicleWrecked?.(this,e)}ray(e,t,s){if(!this.e.alive)return null;let i=this.e,o=this.T,a=Math.cos(-i.rot),n=Math.sin(-i.rot),r=e[0]-i.x,l=e[2]-i.z,h=e[1]-(this.groundY+o.hgt*.55),u=r*a-l*n,d=r*n+l*a,f=t[0]*a-t[2]*n,p=t[0]*n+t[2]*a,g=t[1],v=Math.hypot(f,g,p)||1,m=[f/v,g/v,p/v],x=[u,h,d],b=[o.len/2,o.hgt*.45,o.wid/2],w=0,M=s,E=-1,S=1;for(let k=0;k<3;k++){if(Math.abs(m[k])<1e-9){if(Math.abs(x[k])>b[k])return null;continue}let P=(-b[k]-x[k])/m[k],_=(b[k]-x[k])/m[k],V=-1;if(P>_&&([P,_]=[_,P],V=1),P>w&&(w=P,E=k,S=V*Math.sign(m[k])*-1*-1),M=Math.min(M,_),w>M)return null}let T=[0,0,0];E>=0&&(T[E]=-Math.sign(m[E]));let R=T[0]*Math.cos(i.rot)-T[2]*Math.sin(i.rot),z=T[0]*Math.sin(i.rot)+T[2]*Math.cos(i.rot);return{vehicle:this,dist:w,point:[e[0]+t[0]/v*w,e[1]+t[1]/v*w,e[2]+t[2]/v*w],normal:[R,T[1],z]}}lights(e,t){if(this.mesh.bar&&!this.wrecked){let s=this.siren?1:0,i=Math.floor(t*6)%2;this.mesh.bar[0].material=Ls,this.mesh.bar[1].material=Fs,Ls.emissiveIntensity=s*(i?6:.3),Fs.emissiveIntensity=s*(i?.3:6)}}remove(){this.unseat(),this.e.die("removed")}},$t=class{constructor(e){this.ctx=e,this.list=[],this.trafficTarget=18}spawn(e,t,s,i,o={}){let a=new Is(this.ctx,e,t,s,i,o);return this.list.push(a),a}ray(e,t,s,i){let o=null;for(let a of this.list){if(a===i||!a.e.alive)continue;let n=a.x-e[0],r=a.z-e[2];if(n*n+r*r>(s+6)**2)continue;let l=a.ray(e,t,o?o.dist:s);l&&(!o||l.dist<o.dist)&&(o=l)}return o}blast(e,t,s,i){for(let o of this.list){let a=Math.hypot(o.x-e[0],o.z-e[2]);a<t&&!o.wrecked&&(o.damage(s*(1-a/t)*1.4,i),o.e.vx+=(o.x-e[0])/(a||1)*(1-a/t)*6,o.e.vz+=(o.z-e[2])/(a||1)*(1-a/t)*6,o.vy=Math.max(o.vy,(1-a/t)*5))}}nearest(e,t,s=()=>!0){let i=null,o=t;for(let a of this.list){if(!a.e.alive||!s(a))continue;let n=Math.hypot(a.x-e[0],a.z-e[1]);n<o&&(o=n,i=a)}return i}lanePoint(e,t,s){let i=U(e[0]),o=U(e[1]),a=U(t[0]),n=U(t[1]),r=a-i,l=n-o,h=Math.hypot(r,l)||1;return[i+r*s+l/h*bs,o+l*s+-r/h*bs]}nextNode(e,t){let s=[[1,0],[-1,0],[0,1],[0,-1]].map(([i,o])=>[t[0]+i,t[1]+o]).filter(([i,o])=>i>=0&&o>=0&&i<=N&&o<=N&&!(i===e[0]&&o===e[1]));return this.ctx.g.pick(s.length?s:[e])}spawnTraffic(e,t,s,i){let o=this.ctx.g;for(let a=0;a<20;a++){let n=[o.int(0,N),o.int(0,N)],r=this.nextNode([-9,-9],n),l=o.rand(.15,.85),[h,u]=this.lanePoint(n,r,l),d=Math.hypot(h-e[0],u-e[1]);if(d<t||d>s||i&&i(h,u)||this.nearest([h,u],9))continue;let f=o.pick(["sedan","sedan","sedan","suv","matatu","matatu","boda","boda"]),p=this.spawn(f,h,u,Math.atan2(U(r[1])-U(n[1]),U(r[0])-U(n[0])));p.ai={mode:"traffic",a:n,b:r,speed:f==="boda"?13:f==="matatu"?12:11};let g=new ie(Wt(o));return f==="boda"&&(g.look.hat="helmet"),p.seat(g),p.npcDriver=!0,p}return null}steerTo(e,t,s,i){let o=e.e,n=Math.atan2(s-o.z,t-o.x)-o.rot;n=Math.atan2(Math.sin(n),Math.cos(n));let r=Math.max(-1,Math.min(1,n*2.2)),l=i*(1-Math.min(.55,Math.abs(n)*.7)),[h,u]=e.fwd,d=99,f=this.ctx.g.current;for(let g of f.within("vehicle",[o.x+h*7,o.z+u*7],6)){if(g===o)continue;let v=g.x-o.x,m=g.z-o.z,x=v*h+m*u,b=Math.abs(-v*u+m*h);x>0&&b<2.2&&(d=Math.min(d,x))}for(let g of f.within("person",[o.x+h*6,o.z+u*6],5)){let v=g.x-o.x,m=g.z-o.z,x=v*h+m*u,b=Math.abs(-v*u+m*h);x>0&&b<1.8&&(d=Math.min(d,x))}let p=e.speed<l?1:.1;return d<12&&(p=d<6?-1:0),{throttle:p,steer:r,brake:!1,blocked:d<6}}update(e,t){let s=this.ctx.g.time,i=this.ctx.sky.night;xi.headMat.emissiveIntensity=.2+i*5,xi.tailMat.emissiveIntensity=.4+i*2.5;for(let o of this.list){if(!o.e.alive||o===t)continue;let a={throttle:0,steer:0,brake:!0};if(o.wrecked)a={throttle:0,steer:0,brake:!0};else if(o.ai&&o.driver){let n=o.ai;if(n.mode==="traffic"){let[r,l]=this.lanePoint(n.a,n.b,1),h=U(n.b[0]),u=U(n.b[1]);if(Math.hypot(o.x-h,o.z-u)<10){let d=this.nextNode(n.a,n.b);n.a=n.b,n.b=d}a=this.steerTo(o,r,l,n.speed),n.panic&&(a.throttle=1,n.speed=18),a.blocked?(n.stuck=(n.stuck??0)+e,n.stuck>5&&!n.panic&&(a={throttle:-1,steer:-a.steer,brake:!1}),n.stuck>7&&(n.stuck=0)):n.stuck=0}else if(n.mode==="chase"||n.mode==="route"){let r=n.mode==="chase"?n.target():n.route[n.i];if(r){let l=Math.hypot(r[0]-o.x,r[1]-o.z);n.mode==="route"&&l<12&&(n.i=(n.i+1)%n.route.length);let h=n.mode==="chase"?l<(n.stopAt??14)?0:n.speed??24:n.speed??15,u=this.roadGoal(o,r);a=this.steerTo(o,u[0],u[1],h),n.mode==="chase"&&l<(n.stopAt??14)&&(a.throttle=o.speed>1?-1:0,n.arrived=!0),a.blocked&&n.mode==="chase"&&(a.throttle=.4)}}}o.drive(e,a),o.lights(i,s),o.smoking&&Math.random()<e*8&&this.ctx.fx.smoke.add([o.x,o.e.y+.4,o.z],[0,1.5,0],2,.6,2.5,[.2,.2,.2],.5)}this.list=this.list.filter(o=>o.e.alive)}roadGoal(e,t){let s=this.ctx.world;if(Math.hypot(t[0]-e.x,t[1]-e.z)<30||s.clearLine([e.x,e.z],t))return t;let i=Math.round((e.x+X)/72),o=Math.round((e.z+X)/72),a=[U(i),U(o)],n=1/0;for(let[r,l]of[[0,0],[1,0],[-1,0],[0,1],[0,-1]]){let h=Math.max(0,Math.min(N,i+r)),u=Math.max(0,Math.min(N,o+l)),d=[U(h),U(u)];if(Math.hypot(d[0]-e.x,d[1]-e.z)<9)continue;let f=Math.hypot(d[0]-t[0],d[1]-t[1])+Math.hypot(d[0]-e.x,d[1]-e.z)*.6;f<n&&s.clearLine([e.x,e.z],d)&&(n=f,a=d)}return a}};import*as Le from"three";import{ai as Xt}from"kimi/game";var Xo={gang:["player","police"],police:["gang"],civ:[]},Qo=new Le.Vector3,wi=new Le.Vector3,Re=class{constructor(e,t,s,i,o={}){this.ctx=e,this.role=t,this.team=t==="civ"?"civ":t;let a=o.look??(t==="civ"?Wt(e.g):t==="police"?ne.police:o.boss?ne.gangBoss:ne.gang);this.person=new ie(a),this.weapon=o.weapon?Y[o.weapon]:null,this.person.setWeapon(this.weapon?.model??null);let n=e.world.groundAt(s,i,o.y??0)+.9;this.e=e.g.current.add({at:[s,n,i],radius:.38,capsule:[.38,1.8],height:.9,mesh:this.person.group,tags:["person","npc",this.team],solid:["wall","prop","vehicle","person"],hp:o.hp??(t==="civ"?40:o.boss?420:100),face:"none",speed:t==="civ"?1.4:3.2}),this.e.data.npc=this,this.e.data.team=this.team,this.e.data.armor=o.armor??(t==="police"?30:o.boss?150:0),this.e.data.onHurt=(r,l)=>this.onHurt(r,l),this.e.on("die",()=>this.onDie()),this.state=t==="civ"?"walk":o.state??"patrol",this.home=[s,i],this.post=o.post??null,this.target=null,this.lastSeen=null,this.seeT=0,this.thinkT=Math.random()*.3,this.decideT=0,this.path=null,this.goal=null,this.ammo=this.weapon?.mag??0,this.burst=0,this.fireT=0,this.crouch=!1,this.grenades=o.grenades??(t==="gang"&&Math.random()<.35?1:0),this.accuracy=o.accuracy??(t==="police"?.75:o.boss?.9:.6),this.loop=o.loop??null,this.loopI=0,this.pauseT=0,this.morale=1,this.mission=o.mission??null,this.alertAll=o.alert??!1,this.deadT=0,this.faceRot=o.rot??Math.random()*6.28,this.e.rot=this.e.orot=this.faceRot}get alive(){return this.e.alive}get p(){return[this.e.x,this.e.z]}eye(e=Qo){return e.set(this.e.x,this.e.y+.7,this.e.z)}onHurt(e,t){this.person.flinch=1,t&&t!==this.e&&this.team!=="civ"&&((!this.target||!this.target.alive)&&(this.target=t),this.lastSeen=[t.x,t.z],(this.state==="patrol"||this.state==="idle"||this.state==="investigate")&&(this.state="engage")),this.team==="civ"&&(this.state="flee",this.fleeFrom=t?[t.x,t.z]:this.p),this.morale-=e/200}onDie(){this.deadT=0,this.e.solid=!1,this.e.opts.solid=!1,this.e.untag("person"),this.ctx.onNpcDeath?.(this),this.e.data.knock&&(this.knock=this.e.data.knock),this.person.deadDir=Math.random()<.5?1:-1,this.ctx.fx.bloodPool([this.e.x,0,this.e.z])}hostileTo(e){let t=e.data.team;return this.team==="police"&&t==="player"?this.ctx.heat.stars>0:(Xo[this.team]??[]).includes(t)}sees(e,t=75){let s=e.x-this.e.x,i=e.z-this.e.z,o=Math.hypot(s,i);if(o>t)return!1;if(!(this.state==="engage"||this.state==="cover"||this.alertAll)&&o>6){let l=Math.atan2(i,s)-this.e.rot;if(Math.cos(l)<.25)return!1}let n=[this.e.x,this.e.y+.7,this.e.z],r=e.data.crouch?e.y+.1:e.y+.45;return this.ctx.combat.clear(n,[e.x,r,e.z],[this.e,e])?!0:this.ctx.combat.clear(n,[e.x,e.y+.75,e.z],[this.e,e])}pickTarget(){let e=this.ctx.g.current,t=null,s=1e9;for(let i of e.within("person",this.p,80)){if(i===this.e||!this.hostileTo(i)||i.data.hidden)continue;let o=Math.hypot(i.x-this.e.x,i.z-this.e.z)+(i.data.team==="player"?-8:0);o<s&&this.sees(i)&&(s=o,t=i)}return t}hear(e,t){if(this.alive){if(this.team==="civ"){this.state!=="flee"&&(this.state="flee",this.fleeFrom=[e[0],e[2]],this.e.speed=5.5,this.ctx.audio&&Math.random()<.3&&this.ctx.scream?.(this));return}t&&t!==this.e&&this.hostileTo(t)&&(this.state==="patrol"||this.state==="idle"||this.state==="walk")&&(this.state="investigate",this.goal=[e[0]+(Math.random()-.5)*6,e[2]+(Math.random()-.5)*6],this.path=null,this.investigateT=12)}}moveTo(e,t,s){let i=this.e,o=this.ctx.world;if(!e)return Xt.steerToward(i,[0,0],s,20),!0;if(Math.hypot(e[0]-i.x,e[1]-i.z)<.8)return Xt.steerToward(i,[0,0],s,20),this.path=null,!0;let n=e;if(!o.clearLine([i.x,i.z],e)&&((!this.path||this.pathGoal!==e||(this.repathT-=s)<0)&&(this.path=o.path([i.x,i.z],e,3e3),this.pathGoal=e,this.repathT=2.5),this.path?.length)){for(;this.path.length>1&&Math.hypot(this.path[0][0]-i.x,this.path[0][1]-i.z)<1.2;)this.path.shift();n=this.path[0]}let r=n[0]-i.x,l=n[1]-i.z,h=Math.hypot(r,l)||1,u=r/h*t,d=l/h*t,f=Xt.separate(i,this.ctx.g.current,"person",1.2,.8);return u+=f[0]*.5,d+=f[1]*.5,Xt.steerToward(i,[u,d],s,18),!1}findCover(e){let t=this.ctx.world,s=null,i=-1e9;for(let o of t.covers){let a=Math.hypot(o[0]-this.e.x,o[1]-this.e.z);if(a>22)continue;let n=Math.hypot(o[0]-e[0],o[1]-e[1]);if(n<7||n>45)continue;let r=e[0]-o[0],l=e[1]-o[1];if(o[2]*r+o[3]*l>-.3*Math.hypot(r,l)||!t.navFree(...t.navCell(o[0],o[1]))||(this.ctx.coverTaken.get(o)??this)!==this)continue;let h=-a*1.2-Math.abs(n-18)*.5+Math.random()*3;h>i&&(i=h,s=o)}return s}shootAt(e,t){let s=this.weapon;if(!s)return;if(this.fireT-=t,this.reloadT>0){this.reloadT-=t,this.reloadT<=0&&(this.ammo=s.mag);return}if(this.ammo<=0){this.reloadT=s.reload*1.3,this.ctx.audio.reload?.(s.id);return}if(this.fireT>0)return;if(this.burst<=0){this.burst=s.auto?3+Math.floor(Math.random()*4):1,this.fireT=.6+Math.random()*1.1;return}this.burst--,this.fireT=60/s.rpm*(s.auto?1:1.6+Math.random()),this.ammo--;let i=this.person.muzzleWorld(new Le.Vector3),o=this.eye(new Le.Vector3),a=Math.hypot(e.x-this.e.x,e.z-this.e.z),n=Math.hypot(e.vx,e.vz),r=e.y+(e.data.crouch?.1:.4);wi.set(e.x+e.vx*.15-o.x,r-o.y,e.z+e.vz*.15-o.z).normalize();let l=(.012+a*9e-4+n*.006+(this.e.speedNow>1?.02:0))/this.accuracy*(e.data.team==="player"?this.ctx.difficulty:1);this.person.recoil=1;let h=e.data.team==="player"?.5*this.ctx.difficulty:1;this.ctx.combat.fire(this.e,s,o,wi,i,{spread:l+s.spread*.5,ignore:[],dmgScale:h})}update(e){let t=this.e;if(!t.alive){this.deadT+=e;let p=this.person.group;!p.parent&&this.deadT<30&&(this.ctx.g.three.scene.add(p),p.position.set(t.x,t.y,t.z),p.rotation.set(0,-t.rot,0)),this.knock&&(p.position.x+=this.knock[0]*e,p.position.z+=this.knock[2]*e,this.knock[0]*=.9,this.knock[2]*=.9),this.deadT<2&&this.person.animate(e,{dead:!0}),this.deadT>30&&p.removeFromParent();return}let s=this.ctx.g.current,i=this.ctx.world.groundAt(t.x,t.z,t.y-.9);if(t.data.knock&&(t.vx+=t.data.knock[0],t.vz+=t.data.knock[2],t.data.knock=null),t.y+=(i+.9-t.y)*Math.min(1,e*12),this.thinkT-=e,this.thinkT<=0&&this.team!=="civ"){this.thinkT=.25+Math.random()*.1;let p=this.pickTarget();p?(this.target!==p&&(this.seeT=0),this.target=p,this.lastSeen=[p.x,p.z],this.visible=!0,this.state!=="engage"&&this.state!=="cover"&&(this.state="engage",this.decideT=0,this.ctx.onSpotted?.(this,p))):this.visible=!1}this.visible&&(this.seeT+=e);let o=0,a=!1,n=!1,r=this.target?.alive?this.target:null;switch(this.target&&!r&&(this.target=null,this.state=this.team==="civ"?"walk":"search",this.searchT=6),this.state){case"walk":{if(this.pauseT>0){this.pauseT-=e,this.moveTo(null,0,e);break}let p=this.loop;if(!p){this.moveTo(null,0,e);break}let g=p[this.loopI];this.moveTo(g,t.speed,e)&&(this.loopI=(this.loopI+(this.dir??1)+4)%4,Math.random()<.3&&(this.pauseT=2+Math.random()*5)),o=t.speed;break}case"flee":{let p=this.fleeFrom??this.p,g=t.x-p[0],v=t.z-p[1],m=Math.hypot(g,v)||1;this.fleeGoal??=[t.x+g/m*40,t.z+v/m*40],(this.moveTo(this.fleeGoal,5.5,e)||(this.fleeT=(this.fleeT??0)+e)>14)&&(this.state="cower",this.cowerT=8,this.fleeT=0,this.fleeGoal=null),o=5.5,n=!0;break}case"cower":{this.moveTo(null,0,e),this.crouch=!0,this.cowerT-=e,this.cowerT<=0&&(this.crouch=!1,this.state="walk",this.e.speed=1.4);break}case"idle":case"patrol":{this.post?(this.patrolT=(this.patrolT??0)-e,(!this.goal||this.patrolT<=0)&&(this.goal=[this.post[0]+(Math.random()-.5)*10,this.post[1]+(Math.random()-.5)*10],this.patrolT=6+Math.random()*8),this.moveTo(this.goal,1.3,e)||(o=1.3)):this.moveTo(null,0,e);break}case"investigate":{this.investigateT-=e,a=!0,this.moveTo(this.goal,2.6,e)||this.investigateT<=0?(this.state="patrol",this.goal=null):o=2.6;break}case"search":{this.searchT-=e,a=!0,this.lastSeen&&!this.moveTo(this.lastSeen,2.8,e)&&(o=2.8),this.searchT<=0&&(this.state="patrol",this.lastSeen=null);break}case"engage":case"cover":{if(!r){this.state="search",this.searchT=8;break}a=!0;let p=Math.hypot(r.x-t.x,r.z-t.z);if(this.decideT-=e,this.decideT<=0){this.decideT=2.5+Math.random()*3,this.ctx.coverTaken.delete(this.cover),this.cover=null;let v=t.hp<t.maxHp*.4||this.morale<.4,m=Math.random();if((v||m<.55)&&(this.cover=this.findCover([r.x,r.z])))this.ctx.coverTaken.set(this.cover,this),this.goal=[this.cover[0],this.cover[1]],this.state="cover";else if(p>26||!this.visible&&this.lastSeen){let x=Math.random()<.5?1:-1,b=Math.atan2(r.z-t.z,r.x-t.x)+x*.6;this.goal=[r.x-Math.cos(b)*14,r.z-Math.sin(b)*14],this.state="engage"}else{let x=Math.atan2(r.z-t.z,r.x-t.x)+(Math.random()<.5?1.57:-1.57);this.goal=[t.x+Math.cos(x)*4,t.z+Math.sin(x)*4],this.state="engage"}if(this.grenades>0&&p>10&&p<30&&!this.visible&&this.lastSeen&&Math.random()<.5){this.grenades--;let x=this.eye(new Le.Vector3),b=this.lastSeen[0]-t.x,w=this.lastSeen[1]-t.z,M=Math.hypot(b,w),E=new Le.Vector3(b/M,Math.min(.9,.25+M/60),w/M).normalize(),S={...Y.grenade};this.ctx.combat.launch(t,S,x,E.multiplyScalar(Math.min(1.25,M/18))),this.ctx.say?.(this,"grenade")}}let g=this.moveTo(this.goal,this.state==="cover"&&!this.cover?3:3.6,e);g||(o=3.6,n=p>20),this.state==="cover"&&g?(this.peekT=(this.peekT??0)-e,this.peekT<=0&&(this.peeking=!this.peeking,this.peekT=this.peeking?1.2+Math.random()*1.6:.8+Math.random()*1.4),this.crouch=!this.peeking):this.crouch=!1,this.visible&&this.seeT>.45&&(!this.crouch||this.state!=="cover")&&this.shootAt(r,e);break}}let l=Math.hypot(t.vx,t.vz);a&&r?this.faceRot=Math.atan2(r.z-t.z,r.x-t.x):l>.3&&(this.faceRot=Math.atan2(t.vz,t.vx));let h=this.faceRot-t.rot;h=Math.atan2(Math.sin(h),Math.cos(h)),t.rot+=h*Math.min(1,e*10),t.data.crouch=this.crouch;let u=(t.vx*Math.cos(t.rot)+t.vz*Math.sin(t.rot))/Math.max(.1,l),d=(-t.vx*Math.sin(t.rot)+t.vz*Math.cos(t.rot))/Math.max(.1,l),f=0;a&&r&&(f=Math.atan2(r.y-t.y,Math.hypot(r.x-t.x,r.z-t.z))),this.person.animate(e,{speed:l,fwd:u,side:d,aim:a&&!!this.weapon,pitch:f,crouch:this.crouch,sprint:n})}};import*as re from"three";var W=new re.Vector3,Zo=new re.Vector3,Ns=new re.Vector3,Jo=new re.Vector3,Qt=class{constructor(e,t,s){this.ctx=e;let i=e.g;this.person=new ie(ne.player),this.e=i.current.add({at:[t,.9,s],radius:.4,capsule:[.4,1.8],height:.9,mesh:this.person.group,tags:["person","player"],solid:["wall","prop","vehicle","person"],hp:100,face:"none"});let o=this.e.data;o.team="player",o.isPlayer=!0,o.manualHp=!0,o.armor=0,o.onHurt=(a,n,r)=>this.onHurt(a,n,r),o.onDeath=a=>this.die(a),this.yaw=0,this.pitch=-.08,this.vy=0,this.onGround=!0,this.crouch=!1,this.owned=new Set(["pistol"]),this.ammo=Object.fromEntries(Ce.map(a=>[a,{mag:0,reserve:0}])),this.ammo.pistol={mag:12,reserve:48},this.weapon="pistol",this.lastWeapon="pistol",this.person.setWeapon("pistol"),this.fireT=0,this.reloadT=0,this.bloom=0,this.switchT=0,this.cash=0,this.vehicle=null,this.seatPerson=null,this.camPos=new re.Vector3(t,3,s+5),this.camDist=3.4,this.fov=62,this.scoped=!1,this.aiming=!1,this.hurtT=99,this.hurtFlash=0,this.dead=!1,this.deadT=0,this.lastHitDir=null,this.stepT=0,this.vehLookT=0,this.sens=.0024,this.invertY=!1}get pos(){return[this.e.x,this.e.z]}get def(){return Y[this.weapon]}get armor(){return this.e.data.armor}give(e,t){let s=Y[e],i=!this.owned.has(e);this.owned.add(e);let o=this.ammo[e];i?(o.mag=s.mag,o.reserve+=Math.max(0,(t??s.reserve)-s.mag)):o.reserve=Math.min(o.reserve+(t??s.mag*2),s.reserve*2),i&&e!=="grenade"&&this.equip(e)}equip(e){!this.owned.has(e)||e===this.weapon||e!=="grenade"&&(this.lastWeapon=this.weapon,this.weapon=e,this.reloadT=0,this.switchT=.35,this.person.setWeapon(Y[e].model),this.ctx.audio.click())}cycle(e){let t=Ce.filter(i=>this.owned.has(i)&&i!=="grenade"),s=t.indexOf(this.weapon);this.equip(t[(s+e+t.length)%t.length])}onHurt(e,t,s){this.hurtT=0,this.hurtFlash=Math.min(1,this.hurtFlash+e/40),this.person.flinch=1,t&&(this.lastHitDir=[t.x-this.e.x,t.z-this.e.z]),this.ctx.g.input.vibrate(90,Math.min(1,e/40)),this.ctx.hud?.damageFrom(this.lastHitDir,this.yaw)}die(e){this.dead||(this.dead=!0,this.deadT=0,this.e.hp=0,this.vehicle&&this.exitVehicle(!0),this.person.deadDir=1,this.ctx.onPlayerDown?.(e))}respawn(e){this.dead=!1,this.e.hp=this.e.maxHp,this.e.data.armor=0,this.e.x=this.e.ox=e[0],this.e.z=this.e.oz=e[1],this.e.y=this.e.oy=.9,this.e.vx=this.e.vz=0,this.e.invuln=2,this.person.dead=0,this.hurtFlash=0}enterVehicle(e){let t=this.ctx.g;if(e.driver&&!e.driver.isPlayer){let s=e.unseat();this.ctx.onCarjack?.(e,s)}this.vehicle=e,e.ai=null,e.siren=!1,this.seatPerson=new ie(ne.player),e.T.bike&&(this.seatPerson.look.hat="helmet"),this.seatPerson.setWeapon(null),e.seat(this.seatPerson),e.driver.isPlayer=!0,e.driver.entity=this.e,this.e.visible=!1,this.e.opts.solid=!1,this.e.data.vehicle=e,this.e.tag("invehicle"),this.crouch=!1,this.ctx.audio.click(),this.vehYaw=e.e.rot}exitVehicle(e=!1){let t=this.vehicle;if(!t||Math.abs(t.speed)>9&&!e)return;t.unseat(),this.ctx.audio.engine(t.id,t.type,[t.x,0,t.z],0,!1);let[i,o]=t.fwd,a=t.T.bike?1:t.T.wid/2+.7,n=t.x+-o*a,r=t.z+i*a,l=this.ctx.world;l.navFree(...l.navCell(n,r))||(n=t.x+o*a,r=t.z-i*a),this.e.x=this.e.ox=n,this.e.z=this.e.oz=r,this.e.y=this.e.oy=t.groundY+.9,this.e.vx=t.e.vx*.5,this.e.vz=t.e.vz*.5,this.e.visible=!0,this.e.opts.solid=["wall","prop","vehicle","person"],this.e.data.vehicle=null,this.e.untag("invehicle"),this.vehicle=null,this.yaw=t.e.rot}update(e){let t=this.ctx.g,s=t.input,i=this.e;if(this.dead){this.deadT+=e,i.vx*=.9,i.vz*=.9,this.person.animate(e,{dead:!0});return}this.hurtT+=e,this.hurtFlash=Math.max(0,this.hurtFlash-e*.8),this.hurtT>6&&i.hp<60&&(i.hp=Math.min(60,i.hp+5*e));let o=s.look(e),a=this.scoped?this.def.scope:this.aiming?1.4:1,n=this.sens/a;if(this.yaw+=o.x*n,this.pitch+=(this.invertY?o.y:-o.y)*n,this.pitch=Math.max(-1.25,Math.min(1.1,this.pitch)),Math.abs(o.x)+Math.abs(o.y)>.5&&(this.vehLookT=2),this.vehLookT-=e,s.pressed("interact")&&this.interact(),this.vehicle){this.drive(e);return}let r=s.move("keys"),l=s.down("sprint")&&r.y<-.2&&!this.aiming;s.pressed("crouch")&&(this.crouch=!this.crouch),l&&(this.crouch=!1),this.aiming=s.down("aim")&&this.switchT<=0,this.scoped=this.aiming&&!!this.def.scope;let h=this.crouch?1.7:l?6.8:this.aiming?2.6:3.6,u=Math.cos(this.yaw),d=Math.sin(this.yaw),f=u*-r.y+-d*r.x,p=d*-r.y+u*r.x,g=Math.hypot(f,p),v=g>.05?f/Math.max(1,g)*h:0,m=g>.05?p/Math.max(1,g)*h:0,x=this.onGround?40:6,b=v-i.vx,w=m-i.vz,M=Math.hypot(b,w),E=x*e;M>E?(i.vx+=b/M*E,i.vz+=w/M*E):(i.vx=v,i.vz=m),i.data.knock&&(i.vx+=i.data.knock[0],i.vz+=i.data.knock[2],this.vy=Math.max(this.vy,i.data.knock[1]),i.data.knock=null);let S=i.y-.9,T=this.ctx.world.groundAt(i.x,i.z,S);s.pressed("jump")&&this.onGround&&!this.crouch&&(this.vy=6.4,this.onGround=!1),S>T+.04||this.vy>0?(this.vy-=22*e,i.y+=this.vy*e,this.onGround=!1,i.y-.9<=T&&(this.vy<-13&&this.ctx.combat.hurt(i,(-this.vy-13)*9,null,{fall:!0}),i.y=T+.9,this.vy=0,this.onGround=!0)):(i.y=T+.9,this.vy=0,this.onGround=!0),i.data.crouch=this.crouch;let R=this.aiming||s.down("fire")||this.fireT>-.6,k=(R?this.yaw:g>.1?Math.atan2(i.vz,i.vx):i.rot)-i.rot;k=Math.atan2(Math.sin(k),Math.cos(k)),i.rot+=k*Math.min(1,e*(R?25:10)),this.weapons(e,l);let P=Math.hypot(i.vx,i.vz);this.stepT-=e*P,this.stepT<=0&&this.onGround&&P>.5&&(this.stepT=l?1.9:1.5,this.ctx.audio.step([i.x,i.y-.9,i.z],l));let _=(i.vx*Math.cos(i.rot)+i.vz*Math.sin(i.rot))/Math.max(.1,P),V=(-i.vx*Math.sin(i.rot)+i.vz*Math.cos(i.rot))/Math.max(.1,P);this.person.animate(e,{speed:P,fwd:_,side:V,aim:this.aiming||this.fireT>-.6,pitch:this.pitch,crouch:this.crouch,sprint:l})}aimPoint(e=600){let t=this.ctx.g.current,s=this.ctx.g.three.camera;s.getWorldDirection(W);let i=[s.position.x,s.position.y,s.position.z],o=Math.max(0,s.position.distanceTo(Ns.set(this.e.x,this.e.y+.6,this.e.z))-.5),a=[i[0]+W.x*o,i[1]+W.y*o,i[2]+W.z*o],n=[this.e],r=t.ray3(a,[W.x,W.y,W.z],e,["wall","building","prop","person"],n),l=this.ctx.vehicles.ray(a,[W.x,W.y,W.z],r?r.dist:e,this.vehicle);l&&(!r||l.dist<r.dist)&&(r=l);let h=r?r.dist:e;if(W.y<-1e-4){let u=-a[1]/W.y;u<h&&(h=u,r={ground:!0})}return{point:new re.Vector3(a[0]+W.x*h,a[1]+W.y*h,a[2]+W.z*h),hit:r,dir:W.clone()}}weapons(e,t){let s=this.ctx.g.input,i=this.def,o=this.ammo[this.weapon];this.fireT-=e,this.switchT-=e,this.bloom=Math.max(0,this.bloom-e*2.5);for(let d=1;d<=6;d++)s.pressed("w"+d)&&this.equip(Ce[d-1]);let a=s.wheel();if(a&&this.cycle(a>0?1:-1),s.pressed("next")&&this.cycle(1),s.pressed("prev")&&this.cycle(-1),s.pressed("swap")&&this.equip(this.lastWeapon),s.pressed("grenade")&&this.owned.has("grenade")&&this.ammo.grenade.reserve+this.ammo.grenade.mag>0&&this.fireT<=0){let d=this.ammo.grenade;d.mag>0?d.mag--:d.reserve--,this.ctx.g.three.camera.getWorldDirection(W);let p=new re.Vector3(this.e.x,this.e.y+.7,this.e.z).addScaledVector(W,.6);W.y+=.18,this.ctx.combat.launch(this.e,Y.grenade,p,W.normalize()),this.person.recoil=1,this.fireT=.6}if(this.reloadT>0){if(this.reloadT-=e,this.reloadT<=0){let d=Math.min(i.mag-o.mag,o.reserve);o.mag+=d,o.reserve-=d}return}if(s.pressed("reload")&&o.mag<i.mag&&o.reserve>0||o.mag===0&&o.reserve>0&&s.pressed("fire")){this.reloadT=i.reload,this.ctx.audio.reload(i.id);return}if(t||this.switchT>0||!(i.auto?s.down("fire"):s.pressed("fire"))||this.fireT>0)return;if(o.mag<=0){s.pressed("fire")&&this.ctx.audio.click();return}o.mag--,this.fireT=60/i.rpm;let r=this.aimPoint(i.range??600),l=this.scoped?this.ctx.g.three.camera.position.clone().add(Jo.set(0,-.15,0)):this.person.muzzleWorld(new re.Vector3),h=r.point.clone().sub(l).normalize();if(i.projectile)this.ctx.combat.launch(this.e,i,l.addScaledVector(h,.3),h),this.ctx.audio.shot("rocket",[l.x,l.y,l.z]),this.ctx.fx.muzzle(l,h,!0),this.ctx.fx.smoke.add([l.x-h.x,l.y,l.z-h.z],[-h.x*3,.5,-h.z*3],1.5,.8,3,[.7,.7,.7],.5);else{let d=Math.hypot(this.e.vx,this.e.vz)>.5,f=(this.aiming?i.aimSpread:i.spread)*(d?1.6:1)*(this.crouch?.7:1)+this.bloom,p=this.ctx.combat.fire(this.e,i,l,h,l,{spread:f,ignoreVehicle:this.vehicle,ignore:[]});(p?.entity?.has?.("person")||p?.vehicle)&&this.ctx.hud?.hitmark(p?.entity&&!p.entity.alive),this.bloom=Math.min(.06,this.bloom+(i.auto?.004:.01))}let u=i.kick*(this.aiming?.6:1)*(this.crouch?.7:1);this.pitch+=u*(.8+Math.random()*.4),this.yaw+=(Math.random()-.5)*u*.8,this.person.recoil=1,this.camKick=(this.camKick??0)+u*6,this.ctx.g.input.vibrate(40,Math.min(1,i.kick*15))}drive(e){let t=this.vehicle,s=this.ctx.g.input,i=this.e,o=s.move("keys"),a={throttle:-o.y,steer:o.x,brake:s.down("jump")};t.drive(e,a),i.x=t.x,i.z=t.z,i.y=t.groundY+1.1,i.vx=t.e.vx,i.vz=t.e.vz,i.rot=t.e.rot,this.seatPerson.animate(e,{speed:0,ride:t.T.bike?"bike":"car",aim:this.aiming,pitch:this.pitch}),this.ctx.audio.engine(t.id,t.type,[t.x,0,t.z],Math.min(1,Math.abs(t.speed)/t.T.top*1.3+Math.max(0,a.throttle)*.15),!0),this.vehicle&&(this.aiming=s.down("aim")&&(this.weapon==="pistol"||this.weapon==="rifle"),this.scoped=!1,this.aiming?(this.seatPerson.setWeapon(this.def.model),this.weaponsInCar(e)):this.seatPerson.setWeapon(null),t.wrecked&&this.exitVehicle(!0))}weaponsInCar(e){let t=this.ctx.g.input,s=this.def,i=this.ammo[this.weapon];if(this.fireT-=e,this.reloadT>0){if(this.reloadT-=e,this.reloadT<=0){let h=Math.min(s.mag-i.mag,i.reserve);i.mag+=h,i.reserve-=h}return}if(i.mag===0&&i.reserve>0){this.reloadT=s.reload,this.ctx.audio.reload(s.id);return}if(!(s.auto?t.down("fire"):t.pressed("fire"))||this.fireT>0||i.mag<=0)return;i.mag--,this.fireT=60/s.rpm;let a=this.aimPoint(s.range),n=this.seatPerson.muzzleWorld(new re.Vector3),r=a.point.clone().sub(n).normalize(),l=this.ctx.combat.fire(this.e,s,n,r,n,{spread:s.spread*1.8,ignoreVehicle:this.vehicle,ignore:[]});(l?.entity?.has?.("person")||l?.vehicle)&&this.ctx.hud?.hitmark(l?.entity&&!l.entity.alive),this.pitch+=s.kick*.8}interact(){let e=this.ctx.world;if(this.vehicle){this.exitVehicle();return}let t=e.spots.safehouse;if(t&&Math.hypot(this.e.x-t.locker[0],this.e.z-t.locker[1])<2.6){this.ctx.useLocker?.();return}let s=this.ctx.vehicles.nearest([this.e.x,this.e.z],4,i=>!i.wrecked);s&&this.enterVehicle(s)}promptText(){if(this.dead)return null;if(this.vehicle)return Math.abs(this.vehicle.speed)>9?null:"[F] get out";let e=this.ctx.world.spots.safehouse;if(e&&Math.hypot(this.e.x-e.locker[0],this.e.z-e.locker[1])<2.6)return"[F] locker: ammo, health, save";let t=this.ctx.vehicles.nearest([this.e.x,this.e.z],4,s=>!s.wrecked);return t?t.driver?`[F] take the ${t.type==="boda"?"boda":t.type}`:`[F] ${t.T.bike?"ride":"drive"} the ${t.type==="boda"?"boda":t.type}`:null}camera(e){let t=this.ctx.g.three.camera,s=this.e,i=this.ctx.g.current,o=this.camKick??0;this.camKick=o*Math.exp(-e*14),this.shake=Math.max(0,(this.shake??0)-e*1.8);let a=this.shake*this.shake,n,r,l,h,u=this.yaw,d=this.pitch;if(this.vehicle){let v=this.vehicle;if(this.vehLookT<=0&&Math.abs(v.speed)>2){let m=v.e.rot+(v.speed<-1?Math.PI:0)-this.yaw;m=Math.atan2(Math.sin(m),Math.cos(m)),this.yaw+=m*Math.min(1,e*2.5),this.pitch+=(-.18-this.pitch)*Math.min(1,e*2)}n=Ns.set(v.x,v.groundY+v.T.hgt+.5,v.z),r=v.T.bike?4.6:v.type==="matatu"?9.5:7,l=0,h=64+Math.min(14,Math.abs(v.speed)*.35)}else{n=Ns.set(s.x,s.y+(this.crouch?.25:.65),s.z);let v=this.ctx.g.input.down("sprint")&&Math.hypot(s.vx,s.vz)>4;r=this.aiming?2.1:v?3.9:3.3,l=this.aiming?.72:.5,h=this.aiming?46:v?68:62,this.dead&&(r=6,l=0)}let f=Math.cos(d),p=W.set(Math.cos(u)*f,Math.sin(d),Math.sin(u)*f),g=Zo.set(-Math.sin(u),0,Math.cos(u));if(this.scoped&&!this.vehicle){let v=this.ctx.g.input.down("sprint")?.15:1,m=this.ctx.g.time,x=new re.Vector3(s.x,s.y+(this.crouch?.35:.72),s.z);t.position.copy(x);let b=p.clone();b.x+=Math.sin(m*1.3)*.0016*v,b.y+=Math.sin(m*1.9)*.0012*v,t.lookAt(x.add(b)),t.fov=55/this.def.scope,this.person.group.visible=!1,this.camPos.copy(t.position)}else{this.person.group.visible=!this.vehicle;let v=n.clone().addScaledVector(p,-r).addScaledVector(g,l),m=n.clone().addScaledVector(g,l*.5),x=v.clone().sub(m),b=x.length(),w=i.ray3([m.x,m.y,m.z],[x.x,x.y,x.z],b+.3,["wall","building"]);w&&v.copy(m).addScaledVector(x.normalize(),Math.max(.3,w.dist-.35));let M=this.ctx.world.groundAt(v.x,v.z,v.y)+.3;v.y<M&&(v.y=M),this.camPos.lerp(v,this.vehicle?Math.min(1,e*10):1),t.position.copy(this.camPos),t.position.x+=(Math.random()-.5)*a*.6,t.position.y+=(Math.random()-.5)*a*.5,t.lookAt(this.camPos.clone().add(p)),t.rotateX(o*.02),t.fov+=(h-t.fov)*Math.min(1,e*10)}t.updateProjectionMatrix(),this.ctx.audio.setListener(t.position.x,t.position.y,t.position.z,u)}};var ea=`
.nc{position:absolute;inset:0;pointer-events:none;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#f4f1ea;--u:clamp(11px,1.45vmin,17px);font-size:var(--u);text-shadow:0 1px 3px rgba(0,0,0,.75)}
.nc *{box-sizing:border-box}
.nc-x{position:absolute;left:50%;top:50%;width:0;height:0}
.nc-x i{position:absolute;background:#fff;box-shadow:0 0 2px #000}
.nc-x .t,.nc-x .b{width:2px;height:8px;left:-1px}.nc-x .l,.nc-x .r{height:2px;width:8px;top:-1px}
.nc-x .d{width:3px;height:3px;left:-1.5px;top:-1.5px;border-radius:50%}
.nc-hm{position:absolute;left:50%;top:50%;width:26px;height:26px;margin:-13px;opacity:0;transition:opacity .15s}
.nc-hm:before,.nc-hm:after{content:"";position:absolute;left:12px;top:0;width:2px;height:26px;background:currentColor;transform:rotate(45deg);box-shadow:0 0 3px #000}
.nc-hm:after{transform:rotate(-45deg)}
.nc-scope{position:absolute;inset:0;display:none;background:radial-gradient(circle at 50% 50%,transparent 0,transparent 33vmin,rgba(0,0,0,.6) 33.4vmin,#000 34vmin)}
.nc-scope:before,.nc-scope:after{content:"";position:absolute;background:rgba(0,0,0,.85)}
.nc-scope:before{left:0;right:0;top:50%;height:1px}.nc-scope:after{top:0;bottom:0;left:50%;width:1px}
.nc-scope b{position:absolute;left:50%;top:50%;width:6px;height:6px;margin:-3px;border-radius:50%;background:#e33}
.nc-br{position:absolute;right:2.2em;bottom:2em;text-align:right}
.nc-ammo{font-size:2.6em;font-weight:800;letter-spacing:.02em;font-variant-numeric:tabular-nums}
.nc-ammo small{font-size:.45em;opacity:.7;font-weight:600}
.nc-wname{font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:.85em;opacity:.9}
.nc-slots{display:flex;gap:.35em;justify-content:flex-end;margin-top:.6em}
.nc-slots span{min-width:2.1em;padding:.25em .4em;border-radius:.35em;background:rgba(10,12,18,.55);border:1px solid rgba(255,255,255,.12);font-size:.78em;text-align:center;opacity:.45}
.nc-slots span.own{opacity:.85}.nc-slots span.on{opacity:1;border-color:#ffc24b;color:#ffc24b}
.nc-bl{position:absolute;left:2em;bottom:1.8em}
.nc-map{width:13.5em;height:13.5em;border-radius:50%;overflow:hidden;border:2px solid rgba(255,255,255,.25);box-shadow:0 6px 24px rgba(0,0,0,.5);background:#1b1d1f;position:relative}
.nc-map canvas{width:100%;height:100%;display:block}
.nc-bars{width:13.5em;margin-top:.6em;display:grid;gap:.3em}
.nc-bar{height:.55em;border-radius:1em;background:rgba(255,255,255,.14);overflow:hidden}
.nc-bar i{display:block;height:100%;transition:width .15s}
.nc-cash{position:absolute;right:2.2em;top:1.6em;text-align:right}
.nc-cash b{font-size:1.9em;font-weight:800;color:#8bf59a;font-variant-numeric:tabular-nums}
.nc-heat{font-size:1.5em;letter-spacing:.1em;margin-top:.15em}
.nc-heat i{font-style:normal;opacity:.18}.nc-heat i.on{opacity:1;color:#ffd24b;text-shadow:0 0 8px #ff9b1a}
.nc-heat.flash i.on{animation:nc-blink .5s steps(2) infinite}
@keyframes nc-blink{50%{color:#4b8bff;text-shadow:0 0 8px #2b6bff}}
.nc-time{opacity:.75;margin-top:.2em;letter-spacing:.1em}
.nc-obj{position:absolute;left:2em;top:1.6em;max-width:34em}
.nc-obj h4{margin:0;font-size:.8em;letter-spacing:.22em;text-transform:uppercase;color:#ffc24b}
.nc-obj p{margin:.25em 0 0;font-size:1.15em;font-weight:600}
.nc-prompt{position:absolute;left:50%;bottom:22%;transform:translateX(-50%);padding:.45em 1em;border-radius:2em;background:rgba(10,12,18,.65);font-weight:600;display:none}
.nc-note{position:absolute;left:50%;top:18%;transform:translateX(-50%);text-align:center}
.nc-note div{animation:nc-in .4s ease-out both}
.nc-note h2{margin:0;font-size:2.6em;font-weight:900;letter-spacing:.1em;color:#ffc24b}
.nc-note p{margin:.2em 0;font-size:1.1em;letter-spacing:.2em;text-transform:uppercase}
@keyframes nc-in{from{opacity:0;transform:scale(1.3)}}
.nc-feed{position:absolute;right:2.2em;top:8.5em;display:grid;gap:.3em;justify-items:end}
.nc-feed div{padding:.25em .6em;border-radius:.3em;background:rgba(10,12,18,.5);font-size:.85em;animation:nc-in .25s both}
.nc-dmg{position:absolute;left:50%;top:50%;width:0;height:0}
.nc-dmg i{position:absolute;left:-7em;top:-7em;width:14em;height:14em;border-radius:50%;border:4px solid transparent;border-top-color:rgba(255,40,40,.85);opacity:0;transition:opacity .6s}
.nc-speed{position:absolute;right:2.2em;bottom:9.5em;text-align:right;display:none}
.nc-speed b{font-size:2.6em;font-weight:800;font-variant-numeric:tabular-nums}
.nc-down{position:absolute;inset:0;display:none;align-items:center;justify-content:center;flex-direction:column;background:radial-gradient(ellipse,rgba(60,0,0,.25),rgba(0,0,0,.7))}
.nc-down h1{margin:0;font-size:5em;font-weight:900;letter-spacing:.15em;color:#e8e0d0}
.nc-down p{letter-spacing:.25em;text-transform:uppercase;opacity:.85}
.nc-screen{position:absolute;inset:0;pointer-events:auto;display:flex;align-items:center;justify-content:center;flex-direction:column;background:linear-gradient(180deg,rgba(4,6,12,.2),rgba(4,6,12,.85));text-align:center}
.nc-screen.load{background:#07080c}
.nc-logo{font-size:clamp(42px,9vmin,110px);font-weight:900;letter-spacing:.06em;line-height:.95;margin:0;background:linear-gradient(180deg,#fff 30%,#ffc24b);-webkit-background-clip:text;background-clip:text;color:transparent;filter:drop-shadow(0 6px 30px rgba(255,170,40,.35))}
.nc-tag{letter-spacing:.45em;text-transform:uppercase;opacity:.75;margin:.6em 0 2.2em;font-size:.95em}
.nc-menu{display:grid;gap:.6em;min-width:18em}
.nc-menu button{pointer-events:auto;font:inherit;font-size:1.05em;font-weight:700;letter-spacing:.2em;text-transform:uppercase;padding:.85em 1.4em;border-radius:.5em;border:1px solid rgba(255,255,255,.2);background:rgba(18,20,28,.7);color:#fff;cursor:pointer;transition:background .15s,border-color .15s}
.nc-menu button:hover,.nc-menu button:focus-visible{background:rgba(255,194,75,.18);border-color:#ffc24b;outline:none}
.nc-help{margin-top:2em;display:grid;grid-template-columns:repeat(2,minmax(0,auto));gap:.35em 2.2em;text-align:left;font-size:.9em;opacity:.85}
.nc-help kbd{display:inline-block;min-width:1.7em;padding:.05em .4em;margin-right:.3em;border-radius:.3em;background:rgba(255,255,255,.14);font:600 .85em ui-monospace,monospace;text-align:center}
.nc-loadbar{width:min(60vw,26em);height:4px;border-radius:4px;background:rgba(255,255,255,.12);margin-top:2em;overflow:hidden}
.nc-loadbar i{display:block;height:100%;width:0;background:#ffc24b;transition:width .2s}
.nc-set{display:grid;grid-template-columns:auto 14em;gap:.9em 1.4em;align-items:center;text-align:left;margin-bottom:1.6em}
.nc-set label{letter-spacing:.12em;text-transform:uppercase;font-size:.85em;opacity:.85}
.nc-set select,.nc-set input{pointer-events:auto;font:inherit;padding:.45em;border-radius:.35em;background:#14161d;color:#fff;border:1px solid rgba(255,255,255,.2)}
@media (max-width:700px){.nc-help{grid-template-columns:1fr}.nc-map,.nc-bars{width:9.5em}.nc-map{height:9.5em}}
`,Zt=class{constructor(e){let t=document.createElement("style");t.textContent=ea,document.head.appendChild(t);let s=this.el=document.createElement("div");s.className="nc",s.innerHTML=`
      <div class="nc-scope"><b></b></div>
      <div class="nc-x"><i class="t"></i><i class="b"></i><i class="l"></i><i class="r"></i><i class="d"></i></div>
      <div class="nc-hm"></div>
      <div class="nc-dmg"></div>
      <div class="nc-obj"><h4></h4><p></p></div>
      <div class="nc-cash"><b>KSh 0</b><div class="nc-heat"></div><div class="nc-time"></div></div>
      <div class="nc-feed"></div>
      <div class="nc-note"></div>
      <div class="nc-prompt"></div>
      <div class="nc-speed"><b>0</b> km/h</div>
      <div class="nc-br"><div class="nc-wname"></div><div class="nc-ammo"></div><div class="nc-slots"></div></div>
      <div class="nc-bl"><div class="nc-map"><canvas width="256" height="256"></canvas></div>
        <div class="nc-bars"><div class="nc-bar"><i style="background:#5ee07a;width:100%"></i></div><div class="nc-bar"><i style="background:#4aa8ff;width:0"></i></div></div></div>
      <div class="nc-down"><h1>DOWN</h1><p>back at the safehouse in a moment</p></div>`,e.appendChild(s),this.screens=document.createElement("div"),this.screens.className="nc",e.appendChild(this.screens);let i=a=>s.querySelector(a);this.x=i(".nc-x"),this.hm=i(".nc-hm"),this.scope=i(".nc-scope"),this.dmg=i(".nc-dmg"),this.objH=i(".nc-obj h4"),this.objP=i(".nc-obj p"),this.cash=i(".nc-cash b"),this.heatEl=i(".nc-heat"),this.timeEl=i(".nc-time"),this.feed=i(".nc-feed"),this.note=i(".nc-note"),this.promptEl=i(".nc-prompt"),this.speedEl=i(".nc-speed"),this.speedB=i(".nc-speed b"),this.wname=i(".nc-wname"),this.ammo=i(".nc-ammo"),this.slots=i(".nc-slots"),this.map=i(".nc-map canvas"),this.mctx=this.map.getContext("2d");let o=s.querySelectorAll(".nc-bar i");this.hp=o[0],this.ar=o[1],this.down=i(".nc-down"),this.mapT=0,this.shown={},this.root=e}loading(e,t){this.loadEl||(this.loadEl=document.createElement("div"),this.loadEl.className="nc-screen load",this.loadEl.innerHTML='<h1 class="nc-logo">NYOTA CITY</h1><div class="nc-tag">loading the city</div><div class="nc-loadbar"><i></i></div>',this.screens.appendChild(this.loadEl)),this.loadEl.querySelector("i").style.width=Math.round(e*100)+"%",t&&(this.loadEl.querySelector(".nc-tag").textContent=t),e>=1&&(this.loadEl.remove(),this.loadEl=null)}menu(e){this.closeMenu();let t=document.createElement("div");t.className="nc-screen",t.innerHTML=`<h1 class="nc-logo">NYOTA CITY</h1><div class="nc-tag">a kimi/game open world</div>
      <div class="nc-menu">${e.items.map((s,i)=>`<button data-i="${i}">${s.label}</button>`).join("")}</div>
      <div class="nc-help">${ta.map(([s,i])=>`<div>${s.split(" ").map(o=>`<kbd>${o}</kbd>`).join("")} ${i}</div>`).join("")}</div>`,t.querySelectorAll("button").forEach(s=>s.addEventListener("click",i=>{i.stopPropagation(),e.items[+s.dataset.i].go()})),this.screens.appendChild(t),this.menuEl=t,t.querySelector("button")?.focus()}closeMenu(){this.menuEl?.remove(),this.menuEl=null}settings(e,t,s){this.closeMenu();let i=document.createElement("div");i.className="nc-screen",i.innerHTML=`<h2 style="letter-spacing:.3em;font-weight:800;margin:0 0 1.2em">SETTINGS</h2>
      <div class="nc-set">
        <label for="s-q">Graphics</label><select id="s-q"><option value="auto">Auto (adapts)</option><option value="0">Low</option><option value="1">Medium</option><option value="2">High</option><option value="3">Ultra</option></select>
        <label for="s-s">Mouse sensitivity</label><input id="s-s" type="range" min="0.6" max="4" step="0.1">
        <label for="s-v">Volume</label><input id="s-v" type="range" min="0" max="1" step="0.05">
        <label for="s-i">Invert look</label><select id="s-i"><option value="0">No</option><option value="1">Yes</option></select>
        <label for="s-d">Difficulty</label><select id="s-d"><option value="0.75">Relaxed</option><option value="1">Normal</option><option value="1.35">Hard</option></select>
      </div>
      <div class="nc-menu"><button>Back</button></div>`;let o=n=>i.querySelector(n);o("#s-q").value=e.quality,o("#s-s").value=e.sens,o("#s-v").value=e.volume,o("#s-i").value=e.invert?"1":"0",o("#s-d").value=String(e.difficulty);let a=()=>t({quality:o("#s-q").value,sens:+o("#s-s").value,volume:+o("#s-v").value,invert:o("#s-i").value==="1",difficulty:+o("#s-d").value});i.querySelectorAll("select,input").forEach(n=>n.addEventListener("input",a)),i.querySelector("button").addEventListener("click",n=>{n.stopPropagation(),s()}),this.screens.appendChild(i),this.menuEl=i}notify(e,t,s=3){let i=document.createElement("div");i.innerHTML=`<h2>${e}</h2>${t?`<p>${t}</p>`:""}`,this.note.innerHTML="",this.note.appendChild(i),clearTimeout(this.noteT),this.noteT=setTimeout(()=>i.remove(),s*1e3)}killFeed(e,t="#fff"){let s=document.createElement("div");for(s.textContent=e,s.style.color=t,this.feed.prepend(s);this.feed.childElementCount>5;)this.feed.lastElementChild.remove();setTimeout(()=>s.remove(),5e3)}hitmark(e){this.hm.style.color=e?"#ff4040":"#fff",this.hm.style.transition="none",this.hm.style.opacity="1",requestAnimationFrame(()=>{this.hm.style.transition="opacity .25s",this.hm.style.opacity="0"})}damageFrom(e,t){if(!e)return;let s=document.createElement("i"),i=Math.atan2(e[1],e[0])-t;s.style.transform=`rotate(${i*180/Math.PI+90}deg)`,this.dmg.appendChild(s),requestAnimationFrame(()=>{s.style.opacity="1",setTimeout(()=>{s.style.opacity="0"},500)}),setTimeout(()=>s.remove(),1300)}set objective([e,t]){this.objH.textContent=e??"",this.objP.textContent=t??""}update(e,t){let s=t.player,i=Y[s.weapon],o=s.ammo[s.weapon],a=s.scoped&&!s.vehicle;this.scope.style.display=a?"block":"none";let n=!a&&!s.dead&&(!s.vehicle||s.aiming);if(this.x.style.display=n?"block":"none",n){let u=4+(s.aiming?i.aimSpread??.01:i.spread??.02)*260+s.bloom*300+(Math.hypot(s.e.vx,s.e.vz)>.5?6:0),[d,f,p,g]=this.x.children;d.style.top=-(u+8)+"px",f.style.top=u+"px",p.style.left=-(u+8)+"px",g.style.left=u+"px"}let r=(u,d,f)=>{this.shown[u]!==d&&(this.shown[u]=d,f(d))};r("w",s.reloadT>0?i.name+" \xB7 reloading":i.name,u=>this.wname.textContent=u),r("a",i.projectile==="rocket"?`${o.mag}<small> / ${o.reserve}</small>`:`${o.mag}<small> / ${o.reserve}</small>`,u=>this.ammo.innerHTML=u);let l=Ce.map((u,d)=>`${u}:${s.owned.has(u)}:${s.weapon===u}:${d}`).join()+s.ammo.grenade.mag+s.ammo.grenade.reserve;r("s",l,()=>{this.slots.innerHTML=Ce.map((u,d)=>{let f=s.owned.has(u),p=u==="grenade"?`G ${s.ammo.grenade.mag+s.ammo.grenade.reserve}`:`${d+1} ${Y[u].name.split(" ").pop().slice(0,6)}`;return`<span class="${f?"own":""} ${s.weapon===u?"on":""}">${p}</span>`}).join("")}),this.hp.style.width=Math.max(0,s.e.hp/s.e.maxHp*100)+"%",this.ar.style.width=Math.max(0,s.armor)+"%",r("cash",s.cash,u=>this.cash.textContent="KSh "+u.toLocaleString("en-KE")),r("heat",t.heat+(t.seen?"s":""),()=>{this.heatEl.innerHTML=[1,2,3,4,5].map(u=>`<i class="${u<=t.heat?"on":""}">\u2605</i>`).join(""),this.heatEl.classList.toggle("flash",t.seen&&t.heat>0)}),r("time",t.time,u=>this.timeEl.textContent=u);let h=s.promptText();r("pr",h??"",u=>{this.promptEl.style.display=u?"block":"none",this.promptEl.textContent=u}),s.vehicle?(this.speedEl.style.display="block",this.speedB.textContent=Math.round(Math.abs(s.vehicle.speed)*3.6)):this.speedEl.style.display="none",this.down.style.display=s.dead?"flex":"none",this.el.style.opacity=t.hide?"0":"1",this.mapT-=e,this.mapT<=0&&(this.mapT=1/20,this.drawMap(t))}buildMap(e){let s=1.5058823529411764,i=document.createElement("canvas");i.width=i.height=1024;let o=i.getContext("2d"),a=r=>(r+340)*s;o.fillStyle="#3b3a2f",o.fillRect(0,0,1024,1024),o.fillStyle="#2c2e33";for(let r=0;r<=N;r++){let l=U(r);o.fillRect(a(l-7),a(-X-7),14*s,(X*2+14)*s),o.fillRect(a(-X-7),a(l-7),(X*2+14)*s,14*s)}let n={[D.park]:"#3d6b33",[D.plaza]:"#8a7a62",[D.compound]:"#55534c",[D.garage]:"#6c6c6c"};for(let r=0;r<N;r++)for(let l=0;l<N;l++){let h=e.plan.use[r][l],u=U(r)+7,d=U(l)+7;o.fillStyle="#77746c",o.fillRect(a(u),a(d),(te-14)*s,(te-14)*s),o.fillStyle=n[h]??"#4a4840",o.fillRect(a(u+4),a(d+4),(te-22)*s,(te-22)*s)}for(let r of e.colliders)r.y1-r.y0<2.5||r.y0>1||(o.fillStyle=r.e.has("building")?"#9b968a":"#86826f",o.fillRect(a(r.x0),a(r.z0),Math.max(1,(r.x1-r.x0)*s),Math.max(1,(r.z1-r.z0)*s)));this.mapImg=i,this.mapK=s}drawMap(e){let t=this.mctx,s=256,i=e.player;if(!this.mapImg)return;let o=i.vehicle?.9:1.6,a=this.mapK;t.save(),t.fillStyle="#1b1d1f",t.fillRect(0,0,s,s),t.translate(s/2,s/2),t.rotate(-e.yaw-Math.PI/2),t.scale(o/a,o/a),t.translate(-(i.e.x+340)*a,-(i.e.z+340)*a),t.drawImage(this.mapImg,0,0),t.restore();let n=(d,f)=>{let p=(d-i.e.x)*o,g=(f-i.e.z)*o,v=-e.yaw-Math.PI/2,m=Math.cos(v),x=Math.sin(v);return[s/2+p*m-g*x,s/2+p*x+g*m]},r=([d,f])=>{let p=d-s/2,g=f-s/2,v=Math.hypot(p,g),m=s/2-10;return v>m?[s/2+p/v*m,s/2+g/v*m,!0]:[d,f,!1]};for(let d of e.blips){let[f,p,g]=d.keep?r(n(d.x,d.z)):[...n(d.x,d.z),!1];!d.keep&&Math.hypot(f-s/2,p-s/2)>s/2||(t.fillStyle=d.color,t.strokeStyle="rgba(0,0,0,.7)",t.lineWidth=2,t.beginPath(),d.icon==="mission"?(t.arc(f,p,g?6:8,0,Math.PI*2),t.fill(),t.stroke(),t.fillStyle="#000",t.font="bold 10px sans-serif",t.textAlign="center",t.fillText("!",f,p+3.5)):d.icon==="home"?(t.rect(f-6,p-6,12,12),t.fill(),t.stroke(),t.fillStyle="#000",t.font="bold 9px sans-serif",t.textAlign="center",t.fillText("S",f,p+3)):(t.arc(f,p,d.size??4,0,Math.PI*2),t.fill(),t.stroke()))}t.save(),t.translate(s/2,s/2);let l=(i.vehicle?i.vehicle.e.rot:i.e.rot)-e.yaw;t.rotate(l),t.fillStyle="#fff",t.strokeStyle="#000",t.lineWidth=2,t.beginPath(),t.moveTo(0,-9),t.lineTo(6,7),t.lineTo(0,3),t.lineTo(-6,7),t.closePath(),t.fill(),t.stroke(),t.restore();let[h,u]=r(n(i.e.x,i.e.z-1e3));t.fillStyle="#fff",t.font="bold 12px sans-serif",t.textAlign="center",t.fillText("N",h,u+4)}},ta=[["W A S D","move \xB7 drive"],["Mouse","look \xB7 aim"],["Click","shoot"],["Right-click","aim \xB7 scope"],["Shift","sprint"],["Space","jump \xB7 handbrake"],["C","crouch"],["R","reload"],["1 \u2026 6","weapons (or scroll)"],["G","throw a grenade"],["F","car, boda, locker"],["Esc","pause \xB7 settings"]];import*as F from"three";var sa=new F.MeshBasicMaterial({color:new F.Color(2.2,1.6,.4),transparent:!0,opacity:.16,blending:F.AdditiveBlending,depthWrite:!1,side:F.DoubleSide,toneMapped:!1}),ia=new F.MeshBasicMaterial({color:new F.Color(2.5,1.8,.5),transparent:!0,opacity:.85,blending:F.AdditiveBlending,depthWrite:!1,side:F.DoubleSide,toneMapped:!1}),Bs=class{constructor(e,t,s,i=0,o=2.2){this.g=new F.Group;let a=new F.Mesh(new F.CylinderGeometry(o*.7,o*.7,40,24,1,!0),sa);a.position.y=20;let n=new F.Mesh(new F.RingGeometry(o*.85,o,40).rotateX(-Math.PI/2),ia);n.position.y=.05,this.g.add(a,n),this.g.position.set(t,i,s),this.x=t,this.z=s,this.y=i,this.r=o,e.add(this.g),this.ring=n}update(e){this.ring.scale.setScalar(1+Math.sin(e*3)*.06)}near(e,t,s=this.y,i=1){return Math.hypot(e-this.x,t-this.z)<this.r*i&&Math.abs(s-this.y)<3}remove(){this.g.removeFromParent()}},Mi={armor:{color:4892927,label:"armour +50"},health:{color:6217850,label:"health +50"},ammo:{color:16761419,label:"ammo"}},Jt=class{constructor(e){this.ctx=e,this.list=[]}spawn(e,t,s,i={}){let o=new F.Group,a;if(Y[e])a=Cs(Y[e].model),a.scale.setScalar(1.6);else{let h=Mi[e].color;a=new F.Mesh(e==="armor"?new F.BoxGeometry(.42,.5,.18):e==="health"?new F.BoxGeometry(.42,.3,.3):new F.BoxGeometry(.45,.28,.28),new F.MeshStandardMaterial({color:h,emissive:h,emissiveIntensity:.35,roughness:.4}))}let n=new F.Mesh(new F.RingGeometry(.45,.55,32).rotateX(-Math.PI/2),new F.MeshBasicMaterial({color:new F.Color(1.6,1.4,.6),transparent:!0,opacity:.7,toneMapped:!1,depthWrite:!1}));n.position.y=-.48,o.add(a,n);let r=this.ctx.world.groundAt(t,s,i.y??0)+.55;o.position.set(t,r,s),this.ctx.g.three.scene.add(o);let l={kind:e,g:o,mesh:a,x:t,z:s,y:r,amount:i.amount,life:i.life??1/0,respawn:i.respawn??0,home:[t,s],taken:!1};return this.list.push(l),l}update(e,t){let s=this.ctx.g.time;for(let i of this.list){if(i.taken){i.respawn>0&&(i.respawnT-=e)<=0&&(i.taken=!1,i.g.visible=!0);continue}if(i.life-=e,i.mesh.rotation.y=s*1.8,i.mesh.position.y=Math.sin(s*2.5+i.x)*.08,i.life<=0){i.g.removeFromParent(),i.taken=!0,i.respawn=0;continue}!t.dead&&!t.vehicle&&Math.hypot(t.e.x-i.x,t.e.z-i.z)<1.3&&Math.abs(t.e.y-.9-(i.y-.55))<1.5&&this.take(i,t)}this.list=this.list.filter(i=>!i.taken||i.respawn>0)}take(e,t){let s=this.ctx.hud;if(e.kind==="armor"){if(t.armor>=100)return;t.e.data.armor=Math.min(100,t.armor+50)}else if(e.kind==="health"){if(t.e.hp>=t.e.maxHp)return;t.e.hp=Math.min(t.e.maxHp,t.e.hp+50)}else if(e.kind==="ammo")for(let i of t.owned){let o=Y[i];t.ammo[i].reserve=Math.min(o.reserve*2,t.ammo[i].reserve+o.mag)}else t.give(e.kind,e.amount);s.killFeed("+ "+(Y[e.kind]?Y[e.kind].name:Mi[e.kind].label),"#ffc24b"),this.ctx.audio.pickup(),e.taken=!0,e.g.visible=!1,e.respawn>0?e.respawnT=e.respawn:e.g.removeFromParent()}},es=class{constructor(e){this.ctx=e,this.index=0,this.step=null,this.marker=null,this.enemies=[],this.list=this.define()}get current(){return this.list[this.index]??null}start(e){this.index=e,this.clear(),this.enemies=[];let t=this.current;if(!t){this.freeRoam();return}this.ctx.hud.notify(t.title.toUpperCase(),t.sub,3.5),t.start()}clear(){this.marker?.remove(),this.marker=null,this.step=null}markAt(e,t,s=0,i=2.2){return this.marker?.remove(),this.marker=new Bs(this.ctx.g.three.scene,e,t,s,i),this.marker}objective(e){this.ctx.hud.objective=[this.current?.title??"Free roam",e]}done(e){let t=this.current;this.clear();let s=this.ctx.player;s.cash+=e,this.ctx.hud.notify("MISSION PASSED",`${t.title} \xB7 +KSh ${e.toLocaleString("en-KE")}`,4),this.ctx.audio.pickup(),this.ctx.save(),this.ctx.g.current.after(4.5,()=>this.start(this.index+1))}gang(e,t,s={}){let i=new Re(this.ctx,"gang",e,t,{weapon:s.weapon??this.ctx.g.pick(["pistol","rifle","rifle","shotgun"]),post:s.post??[e,t],...s});return this.ctx.npcs.push(i),this.enemies.push(i),i}aliveEnemies(){return this.enemies.filter(e=>e.alive)}define(){let e=this.ctx,t=e.world,s=()=>e.player,i=t.spots.safehouse;return[{title:"Karibu Nyota",sub:"welcome home",start:()=>{this.markAt(i.inside[0],i.inside[1]),this.objective("Get to your safehouse (the yellow light)"),this.step=()=>{this.marker?.near(s().e.x,s().e.z,0,1.3)&&(this.markAt(i.locker[0],i.locker[1]+1,0,1.2),this.objective("Open the locker [F] \u2014 a rifle is waiting"),this.step=()=>{s().owned.has("rifle")&&this.done(500)})}}},{title:"Boda Ride",sub:"the city is faster on two wheels",start:()=>{let[o,a]=i.garage,n=e.vehicles.spawn("boda",o+2,a,Math.PI/2);this.objective("Take the boda-boda outside the garage door [F]"),this.markAt(n.x,n.z,0,1.6);let r=t.spots.market?.[1]??t.spots.plaza;this.step=()=>{s().vehicle&&(this.markAt(r[0],r[1]-2,0,3),this.objective("Ride to the market"),this.step=()=>{this.marker.near(s().e.x,s().e.z,0,1.4)&&this.done(1e3)})}}},{title:"The Warehouse",sub:"Kivuli's people are in the warehouse",start:()=>{let o=t.spots.compound;this.markAt(o.gate[0],o.gate[1]-6,0,3),this.objective("Drive to the Kivuli warehouse"),this.step=()=>{if(Math.hypot(s().e.x-o.gate[0],s().e.z-o.gate[1])<45){this.clear();let[a,n]=o.center;[[a-8,n-10],[a+9,n-12],[a,n-17],[a-15,n-3],[a+14,n-2],[a-6,n+12],[a+6,n+14],[a,n+19],[a-11,n+18],[a+11,n+8]].forEach((l,h)=>this.gang(l[0],l[1],{grenades:h%4===0?2:0,weapon:h%3===0?"shotgun":h%3===1?"rifle":"pistol"})),this.objective("Clear the warehouse and its yard"),this.step=()=>{let l=this.aliveEnemies().length;this.objective(`Clear the warehouse \u2014 ${l} Kivuli left`),l===0&&(e.pickups.spawn("sniper",a+2,n+12,{amount:20}),e.pickups.spawn("shotgun",a-2,n+12,{amount:24}),e.pickups.spawn("armor",a,n+14),this.done(3e3))}}}}},{title:"High Ground",sub:"lookouts in the plaza",start:()=>{let o=t.spots.garage;s().owned.has("sniper")||s().give("sniper",20),this.markAt(o.roof[0],o.roof[1],8,2.5),this.objective("Climb the parking garage (ramp on the east side) to the roof"),this.step=()=>{if(this.marker.near(s().e.x,s().e.z,s().e.y-.9,1.4)){this.clear();let[a,n]=t.spots.plaza;[[a-14,n-12],[a+12,n-15],[a-18,n+6],[a+16,n+10],[a-4,n+18],[a+3,n-20]].forEach(l=>this.gang(l[0],l[1],{weapon:"rifle",state:"patrol",accuracy:.5})),this.objective("Snipe the 6 lookouts in the plaza (right-click to scope)"),this.step=()=>{let l=this.aliveEnemies().length;l<6&&this.objective(`Lookouts left: ${l}`),l===0&&this.done(3e3)},e.hud.killFeed("hold Shift to steady the scope","#bfe3ff")}}}},{title:"Convoy",sub:"three armoured cars carry Kivuli's money",start:()=>{let[o,a]=i.garage;e.pickups.spawn("rocket",o+1,a-3,{amount:6}),e.pickups.spawn("armor",o+1,a+3),this.markAt(o+1,a-3,0,1.4),this.objective("Pick up the rocket launcher by the safehouse garage"),this.step=()=>{if(s().owned.has("rocket")){this.clear();let n=[[2,2],[6,2],[6,6],[2,6]].map(([l,h])=>[U(l),U(h)]),r=[];for(let l=0;l<4;l++){let h=n[l],u=n[(l+1)%4];for(let d=0;d<1;d+=.25)r.push([h[0]+(u[0]-h[0])*d,h[1]+(u[1]-h[1])*d])}this.convoy=[];for(let l=0;l<3;l++){let h=r[l*2%r.length],u=e.vehicles.spawn("armored",h[0]-l*9,h[1],0);u.ai={mode:"route",route:r,i:(l*2+1)%r.length,speed:13},u.seat(new ie(ne.gang)),u.e.data.team="gang",u.onDamage=()=>{u.unloaded||(u.unloaded=!0,u.ai={mode:"chase",target:()=>[s().e.x,s().e.z],speed:18,stopAt:18},e.g.current.after(1.5,()=>{for(let d=0;d<2;d++){let[f,p]=u.fwd;this.gang(u.x-p*(2+d),u.z+f*(2+d),{weapon:"rifle",alert:!0,state:"engage"})}}))},this.convoy.push(u)}this.objective("Destroy the 3 armoured cars (rockets, or a lot of bullets)"),this.step=()=>{let l=this.convoy.filter(u=>!u.wrecked).length;this.objective(`Armoured cars left: ${l}`);let h=this.convoy.find(u=>!u.wrecked);h&&(this.convoyBlip=[h.x,h.z]),l===0&&(this.convoyBlip=null,this.done(5e3))}}}}},{title:"Kivuli's Tower",sub:"end it",start:()=>{let o=t.spots.gangTower;this.markAt(o.court[0],o.court[1]-10,0,3),this.objective("Go to Kivuli's tower"),this.step=()=>{if(Math.hypot(s().e.x-o.court[0],s().e.z-o.court[1])<50){this.clear();let[a,n]=o.inside;[[a-12,o.court[1]],[a+12,o.court[1]],[a-5,o.court[1]+2],[a+5,o.court[1]+2],[a-18,o.court[1]-4],[a+18,o.court[1]-4]].forEach((l,h)=>this.gang(l[0],l[1],{weapon:h%2?"rifle":"shotgun",grenades:h===0?2:0})),[[a-9,n-6],[a+9,n-6],[a-9,n+7],[a+9,n+7],[a,n+4],[a-4,n-8]].forEach(l=>this.gang(l[0],l[1],{weapon:"rifle",armor:50})),this.boss=this.gang(a,n+11,{boss:!0,weapon:"rifle",grenades:4,armor:150,accuracy:.85}),this.objective("Take Kivuli's tower: the forecourt, the lobby, then Kivuli"),this.step=()=>{let l=this.aliveEnemies().length;this.objective(this.boss.alive?`Kivuli's people left: ${l-1} \xB7 Kivuli is in the lobby`:`Kivuli is down \u2014 ${l} left`),l===0&&(this.done(1e4),e.g.current.after(1,()=>e.hud.notify("NYOTA CITY IS YOURS","free roam: the city keeps living",6)))}}}}}]}freeRoam(){this.objective("Free roam \u2014 Kivuli's leftovers still hold the compounds"),this.ctx.hud.objective=["Free roam","Drive, fight, explore. Kivuli's leftovers turn up at the compounds."],this.roamT=30}update(e){if(this.marker?.update(this.ctx.g.time),this.step?.(),!this.current&&(this.roamT-=e)<=0){this.roamT=90;let t=this.ctx.g.pick(this.ctx.world.spots.compounds);if(this.aliveEnemies().length<8)for(let s=0;s<4;s++)this.gang(t.yard[0]+this.ctx.g.rand(-8,8),t.yard[1]+this.ctx.g.rand(-6,6))}}blips(){let e=[];return this.marker&&e.push({x:this.marker.x,z:this.marker.z,color:"#ffc24b",icon:"mission",keep:!0}),this.convoyBlip&&e.push({x:this.convoyBlip[0],z:this.convoyBlip[1],color:"#ffc24b",icon:"mission",keep:!0}),e}},ts=class{constructor(e){this.ctx=e,this.level=0,this.coolT=0,this.spawnT=0,this.seen=!1,this.units=[]}add(e,t){let s=this.level;this.level=Math.min(5,Math.max(this.level,0)+e),Math.floor(this.level)>Math.floor(s)&&this.ctx.hud.killFeed(`heat ${"\u2605".repeat(Math.floor(this.level))} \u2014 ${t}`,"#ffd24b"),this.coolT=0}get stars(){return Math.floor(this.level)}update(e){let t=this.ctx,s=t.player,i=this.stars;if(this.seen=t.npcs.some(n=>n.alive&&n.team==="police"&&n.target===s.e&&n.visible),i===0){t.audio.siren(!1),this.units=this.units.filter(n=>n.e.alive);return}this.seen?this.coolT=0:(this.coolT+=e,this.coolT>10+i*4&&(this.level=Math.max(0,this.level-1),this.coolT=0,t.hud.killFeed(this.stars?`heat down to ${"\u2605".repeat(this.stars)}`:"you lost the police","#8bf59a"))),this.spawnT-=e,this.units=this.units.filter(n=>n.e.alive&&!n.wrecked);let o=t.npcs.filter(n=>n.alive&&n.team==="police").length;if(this.spawnT<=0&&this.units.length<Math.min(4,i)&&o<i*3){this.spawnT=Math.max(5,14-i*2);let n=t.spawnPoliceCar();n&&this.units.push(n)}for(let n of this.units)if(n.ai?.arrived&&!n.unloaded&&Math.abs(n.speed)<2){n.unloaded=!0;let[r,l]=n.fwd,h=i>=3?3:2;for(let u=0;u<h;u++){let d=new Re(t,"police",n.x+-l*(2.2+u*.6)*(u%2?-1:1),n.z+r*(2.2+u*.6)*(u%2?-1:1),{weapon:i>=3?"rifle":u===0?"shotgun":"pistol",alert:!0,armor:i>=3?60:20});d.target=s.e,d.state="engage",t.npcs.push(d)}n.unseat(),n.ai=null}let a=this.units.find(n=>Math.hypot(n.x-s.e.x,n.z-s.e.z)<150);t.audio.siren(!!a,a?[a.x,0,a.z]:null),t.audio.heat=i}},ss=class{constructor(e){this.ctx=e,this.civT=0,this.carT=0,this.civTarget=22,this.parked=!1}outOfView(e,t){let s=this.ctx.g.three.camera,i=new F.Vector3;s.getWorldDirection(i);let o=e-s.position.x,a=t-s.position.z,n=Math.hypot(o,a);return(o*i.x+a*i.z)/(n||1)<.35||n>140}update(e){let t=this.ctx,s=t.player,i=t.world,o=t.g;if(!this.parked){this.parked=!0;for(let[a,n,r]of i.parking)t.vehicles.spawn(o.pick(["sedan","sedan","suv","matatu"]),a+2,n,r)}if(this.civT-=e,this.civT<=0){this.civT=.7;let a=t.npcs.filter(n=>n.role==="civ"&&n.alive);for(let n of a)Math.hypot(n.e.x-s.e.x,n.e.z-s.e.z)>170&&(n.e.die("despawn"),n.despawned=!0,n.person.group.removeFromParent());if(a.length<this.civTarget){let n=i.sidewalkLoops.filter(p=>Math.hypot((p[0][0]+p[2][0])/2-s.e.x,(p[0][1]+p[2][1])/2-s.e.z)<140),r=n.length?o.pick(n):null,l=o.int(0,3),h=(l+1)%4,u=o.rand(),d=r?[r[l][0]+(r[h][0]-r[l][0])*u,r[l][1]+(r[h][1]-r[l][1])*u]:[1e9,1e9],f=Math.hypot(d[0]-s.e.x,d[1]-s.e.z);if(r&&f>35&&f<140&&(this.outOfView(d[0],d[1])||f>70)){let p=new Re(t,"civ",d[0],d[1],{loop:r});p.loopI=o.int(0,3),p.dir=o.chance(.5)?1:-1,t.npcs.push(p)}}}if(this.carT-=e,this.carT<=0){this.carT=.8;for(let n of t.vehicles.list)n.npcDriver&&n.driver&&Math.hypot(n.x-s.e.x,n.z-s.e.z)>240&&n.remove();t.vehicles.list.filter(n=>n.npcDriver&&n.driver).length<t.vehicles.trafficTarget&&t.vehicles.spawnTraffic([s.e.x,s.e.z],70,200,(n,r)=>!this.outOfView(n,r))}Math.random()<e&&(t.npcs=t.npcs.filter(a=>a.alive||a.deadT<31&&!a.despawned))}};function yi(c){let e=c.player,t=c.g;for(let s=0;s<20;s++){let i=t.int(0,N),o=t.int(0,N),a=U(i),n=U(o),r=Math.hypot(a-e.e.x,n-e.e.z);if(r>70&&r<170&&c.population.outOfView(a,n))return[a,n]}return null}var Si="nyota-city:save",Ri="nyota-city:settings",Q={quality:"auto",sens:1.6,volume:.8,invert:!1,difficulty:1},vt=new URLSearchParams(location.search),K=oa({title:"NYOTA CITY",mode:"3d",preset:"custom",bloom:!1,floor:!1,world:[700,700],titleScreen:!1,adaptive:Q.quality==="auto"?{lowerAbove:30,raiseBelow:13}:!1,quality:Q.quality==="auto"?"auto":["low","medium","high","high"][+Q.quality],howto:["Move [W][A][S][D] \xB7 look with the mouse \xB7 [Click] shoot \xB7 [Right-click] aim","[Shift] sprint \xB7 [Space] jump / handbrake \xB7 [C] crouch \xB7 [R] reload","[1]\u2013[6] weapons \xB7 [G] grenade \xB7 [F] cars, bodas, locker","[Esc] pause and settings"]}),q=K.input;q.bind("fire",["mouse:0","pad:7"],{replace:!0});q.bind("aim",["mouse:2","pad:6"]);q.bind("jump",["Space","pad:0"],{replace:!0});q.bind("sprint",["ShiftLeft","ShiftRight","pad:10"]);q.bind("crouch",["KeyC","ControlLeft","pad:11"]);q.bind("reload",["KeyR","pad:2"]);q.bind("interact",["KeyF","KeyE","pad:3"]);q.bind("grenade",["KeyG","pad:5"]);q.bind("next",["pad:15"]);q.bind("prev",["pad:14","pad:4"]);q.bind("swap",["KeyQ"]);for(let c=1;c<=6;c++)q.bind("w"+c,["Digit"+c]);q.wantsAimStick=!0;K.scene("play",c=>{c.camera.manual();let e=new Zt(K.root);e.loading(.05,"pouring the roads");let t=K.three,s={g:K,hud:e,audio:new Bt,npcs:[],coverTaken:new Map,difficulty:Q.difficulty,ready:!1,inMenu:!0};window.__nyota=s,c.after(.05,()=>{let i=new St(t.scene,t.renderer);s.sky=i,vt.has("hour")&&(i.hour=+vt.get("hour"));let o=new Ft(K,i);s.pipe=o,s.world=new Tt(K,c,Vs(2026),i),i.hills=s.world.hills,e.buildMap(s.world),s.fx=new Nt(K,c),s.vehicles=new $t(s),s.combat=new Gt(s),s.pickups=new Jt(s);let a=s.world.spots.safehouse;s.player=new Qt(s,a.door[0],a.door[1]-3),s.player.yaw=Math.PI/2,s.missions=new es(s),s.heat=new ts(s),s.population=new ss(s),aa(s),s.world.pickupSpots.forEach((n,r)=>s.pickups.spawn(["armor","health","ammo"][r%3],n[0],n[1],{respawn:120})),ra(s),Gs(s),s.ready=!0,e.loading(1),s.saved=null,Promise.all([Ti(Si,null),Ti(Ri,{})]).then(([n,r])=>{s.saved=n,Q={...Q,...r??{}},Gs(s),ki(s)}),vt.has("autostart")&&setTimeout(()=>Us(s,!1),300)}),c.on("tick",i=>{if(!s.ready)return;let{player:o}=s;s.combat.update(i),s.inMenu?o.person.animate(i,{speed:0,fwd:1,side:0}):o.update(i),s.vehicles.update(i,o.vehicle);let a=o.e.x,n=o.e.z;for(let r of s.npcs)r.role==="civ"&&r.alive&&Math.hypot(r.e.x-a,r.e.z-n)>90&&Math.random()<.5||r.update(i);s.inMenu||(s.missions.update(i),s.heat.update(i)),s.population.update(i),s.pickups.update(i,o),o.dead&&o.deadT>4.2&&!s.respawning&&na(s)})});function aa(c){let{g:e,hud:t}=c,s=c.heat;c.noise=(i,o,a)=>{for(let n of c.npcs)n.alive&&Math.hypot(n.e.x-i[0],n.e.z-i[2])<o&&n.hear(i,a);a===c.player.e&&s.stars===0&&c.npcs.find(r=>r.alive&&r.team==="police"&&Math.hypot(r.e.x-i[0],r.e.z-i[2])<45)&&s.add(1,"shots fired near police")},c.whiz=(i,o,a,n)=>{if(n===c.player.e)for(let r of c.npcs){if(!r.alive)continue;let l=r.e.x-i[0],h=r.e.z-i[2],u=l*o[0]+h*o[2];if(u<0||u>a+2)continue;let d=i[0]+o[0]*u-r.e.x,f=i[2]+o[2]*u-r.e.z;Math.hypot(d,f)<3&&r.hear([r.e.x,r.e.y,r.e.z],n)}},c.shake=(i,o)=>{let a=Math.hypot(i[0]-c.player.e.x,i[2]-c.player.e.z);c.player.shake=Math.max(c.player.shake??0,o*Math.max(0,1-a/60))},c.onKill=(i,o,a)=>{let n=i.data.npc;if(!n)return;if(o===c.player.e){let l=a?.head?" \xB7 head shot":"";if(n.team==="gang"){let h=n.person.look===ne.gangBoss?2e3:100;c.player.cash+=h,t.killFeed((h>100?"Kivuli is down":"Kivuli member down")+l+` \xB7 +${h}`,"#ff8a6a")}else n.team==="police"?(s.add(1,"an officer is down"),t.killFeed("officer down"+l,"#7fb2ff")):(s.add(1,"a civilian was killed"),t.killFeed("civilian killed","#ff4d4d"))}if(n.team!=="civ"){let l=e.rand(),h=i.x+e.rand(-.6,.6),u=i.z+e.rand(-.6,.6);l<.3&&n.weapon?c.pickups.spawn(n.weapon.id,h,u,{amount:n.weapon.mag*2,life:60,y:i.y-.9}):l<.55?c.pickups.spawn("ammo",h,u,{life:60,y:i.y-.9}):l<.68&&c.pickups.spawn("armor",h,u,{life:60,y:i.y-.9})}},c.onVehicleWrecked=(i,o)=>{o===c.player.e&&i.type==="police"&&s.add(1,"a police car destroyed"),o===c.player.e&&(c.player.cash+=i.type==="armored"?500:0)},c.onCarjack=(i,o)=>{if(!o)return;let[a,n]=i.fwd,r=new Re(c,i.type==="police"?"police":"civ",i.x+n*2.2,i.z-a*2.2,{look:o.look,weapon:i.type==="police"?"pistol":null});r.state=i.type==="police"?"engage":"flee",r.fleeFrom=[i.x,i.z],c.npcs.push(r),i.type==="police"?s.add(2,"a police car stolen"):c.noise([i.x,1,i.z],15,c.player.e)},c.useLocker=()=>{let i=c.player;i.owned.has("rifle")||i.give("rifle",120);for(let o of i.owned){let a=Y[o];i.ammo[o].reserve=Math.max(i.ammo[o].reserve,a.reserve)}i.owned.has("grenade")?i.ammo.grenade.reserve=Math.max(i.ammo.grenade.reserve,3):i.give("grenade",3),i.e.hp=i.e.maxHp,i.e.data.armor=Math.max(i.armor,50),t.killFeed("locker: ammo, health and armour \xB7 game saved","#ffc24b"),c.audio.reload("rifle"),c.save()},c.onPlayerDown=()=>{e.slowmo(.3,2.5),t.killFeed("you went down","#ff4d4d")},c.spawnPoliceCar=()=>{let i=yi(c);if(!i)return null;let o=c.player,a=c.vehicles.spawn("police",i[0],i[1],Math.atan2(o.e.z-i[1],o.e.x-i[0]));return a.seat(new ie(ne.police)),a.siren=!0,a.ai={mode:"chase",target:()=>[o.e.x,o.e.z],speed:22+s.stars*2,stopAt:16},a},c.save=()=>{let i=c.player;Ei(Si,{mission:c.missions.index,owned:[...i.owned],ammo:i.ammo,cash:i.cash,armor:i.armor,hour:c.sky.hour})},e.on("pause",()=>{c.inMenu||(e.ui.hideCard(),zi(c))}),e.on("resume",()=>{c.inMenu||t.closeMenu()}),document.addEventListener("pointerlockchange",()=>{!document.pointerLockElement&&e.state==="play"&&!c.inMenu&&!q.isTouch&&e.pause()}),e.root.addEventListener("click",()=>{!c.inMenu&&e.state==="play"&&!q.freeLook&&q.lockPointer()}),document.addEventListener("pointerlockerror",()=>{q.freeLook||(q.freeLook=!0,t.killFeed("mouse look without capture: keep the cursor inside the window","#bfe3ff"))})}function Us(c,e){let{player:t,hud:s}=c;c.inMenu=!1,s.closeMenu(),c.audio.start(),q.lockPointer();let i=0;if(e&&c.saved){let o=c.saved;i=o.mission??0;for(let a of o.owned??[])t.owned.add(a);Object.assign(t.ammo,o.ammo??{}),t.cash=o.cash??0,t.e.data.armor=o.armor??0,o.hour!==void 0&&(c.sky.hour=o.hour),t.owned.has("rifle")&&t.equip("rifle")}vt.has("mission")&&(i=+vt.get("mission")),c.missions.start(i)}function na(c){c.respawning=!0;let{player:e,world:t,hud:s}=c,i=t.spots.safehouse;e.cash=Math.floor(e.cash*.9),c.heat.level=0,e.respawn(i.inside),s.killFeed("back at the safehouse (\u221210 % cash)","#ffc24b"),c.respawning=!1}function ki(c){let e=[];c.saved&&e.push({label:"Continue",go:()=>Us(c,!0)}),e.push({label:c.saved?"New game":"Play",go:()=>Us(c,!1)}),e.push({label:"Settings",go:()=>Ai(c,()=>ki(c))}),e.push({label:"Full screen",go:()=>{document.documentElement.requestFullscreen?.().catch(()=>{})}}),c.hud.menu({items:e})}function zi(c){c.hud.menu({items:[{label:"Resume",go:()=>{K.resume(),q.lockPointer()}},{label:"Settings",go:()=>Ai(c,()=>zi(c))},{label:"Save",go:()=>{c.save(),c.hud.killFeed("game saved","#ffc24b")}},{label:"Full screen",go:()=>{document.documentElement.requestFullscreen?.().catch(()=>{})}}]})}function Ai(c,e){c.hud.settings(Q,t=>{Q={...Q,...t},Gs(c),Ei(Ri,Q)},e)}function Gs(c){let e=c.player;e.sens=.0015*Q.sens,e.invertY=Q.invert,c.audio.volume=Q.volume,c.difficulty=Q.difficulty,Q.quality==="auto"?K.opts.adaptive={lowerAbove:30,raiseBelow:13}:(K.opts.adaptive=!1,K.three.setQuality(+Q.quality))}function ra(c){let e=K.three.scene,t=[],s=[];for(let r=0;r<6;r++){let l=new ke.PointLight(16763274,0,18,1.8);e.add(l),t.push(l)}for(let r=0;r<4;r++){let l=new ke.PointLight(16773336,0,16,1.6);e.add(l),s.push(l)}let i=new ke.SpotLight(16773840,0,60,.5,.5,1.4);e.add(i,i.target);let o=0,a=K.three.camera,n={a:.6};c.pipe.beforeRender=r=>{let{player:l,sky:h,world:u,hud:d,fx:f}=c,p=new ke.Vector3(l.e.x,0,l.e.z);if(h.update(K.state==="paused"?0:r,c.inMenu?new ke.Vector3(u.spots.plaza[0],0,u.spots.plaza[1]):p),c.inMenu){n.a+=r*.04;let[b,w]=u.spots.plaza;a.position.set(b+Math.cos(n.a)*85,34,w+Math.sin(n.a)*85),a.lookAt(b,8,w),a.fov=55,a.updateProjectionMatrix(),c.audio.setListener(a.position.x,a.position.y,a.position.z,0)}else l.camera(r);f.update(r,K.time);let g=u.indoors(a.position.x,a.position.z),v=c.insideK??=0;if(c.insideK=v+((g&&a.position.y<g.h?1:0)-v)*Math.min(1,r*4),h.hemi.intensity*=1-.7*c.insideK,K.three.scene.environmentIntensity=(.15+.45*(1-h.night))*(1-.75*c.insideK),K.three.renderer.toneMappingExposure*=1-.25*c.insideK,o-=r,o<=0){o=.4;let b=a.position;u.lampLights.map(E=>[E,(E[0]-b.x)**2+(E[2]-b.z)**2]).sort((E,S)=>E[1]-S[1]).slice(0,t.length).forEach(([E],S)=>t[S].position.set(E[0],E[1],E[2]));let M=[];for(let E of u.interiors)if(Math.hypot((E.x0+E.x1)/2-l.e.x,(E.z0+E.z1)/2-l.e.z)<40)for(let T of E.lamps)M.push([T,Math.hypot(T[0]-l.e.x,T[2]-l.e.z)]);M.sort((E,S)=>E[1]-S[1]),s.forEach((E,S)=>{let T=M[S];T?(E.position.set(T[0][0],T[0][1],T[0][2]),E.intensity=2.6+2*c.sky.night):E.intensity=0})}for(let b of t)b.intensity=h.night*22;let m=l.vehicle;if(m&&h.night>.25){let[b,w]=m.fwd;i.position.set(m.x+b*m.T.len*.5,m.groundY+.8,m.z+w*m.T.len*.5),i.target.position.set(m.x+b*30,0,m.z+w*30),i.intensity=160*h.night}else i.intensity=0;for(let b of c.npcs)b.alive&&(b.person.group.visible=Math.hypot(b.e.x-a.position.x,b.e.z-a.position.z)<160);c.pipe.hurt=Math.max(l.hurtFlash*.8,l.e.hp<30?.55:0),c.audio.update();let x=[{x:u.spots.safehouse.door[0],z:u.spots.safehouse.door[1],color:"#fff",icon:"home",keep:!0},...c.missions.blips()];for(let b of c.npcs)!b.alive||b.team==="civ"||(b.state==="engage"||b.state==="cover"||c.missions.enemies.includes(b))&&x.push({x:b.e.x,z:b.e.z,color:b.team==="police"?"#4b8bff":"#ff4040"});for(let b of c.heat.units)x.push({x:b.x,z:b.z,color:"#4b8bff",size:5});d.update(r,{player:l,heat:c.heat.stars,seen:c.heat.seen,time:h.timeText,yaw:c.inMenu?n.a:l.yaw,blips:x,hide:c.inMenu})}}K.start("play");
