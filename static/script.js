(function(){
  var c=document.getElementById('bgCanvas');
  if(!c)return;
  var ctx=c.getContext('2d');
  function resize(){c.width=window.innerWidth;c.height=window.innerHeight}
  resize();
  window.addEventListener('resize',resize);
  var particles=[];
  for(var i=0;i<35;i++){
    particles.push({x:Math.random()*c.width,y:Math.random()*c.height,r:Math.random()*1.5+0.5,dx:(Math.random()-0.5)*0.3,dy:(Math.random()-0.5)*0.3,o:Math.random()*0.3+0.1});
  }
  function draw(){
    ctx.clearRect(0,0,c.width,c.height);
    for(var i=0;i<particles.length;i++){
      var p=particles[i];
      ctx.beginPath();
      ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle='rgba(251,191,36,'+p.o+')';
      ctx.fill();
      p.x+=p.dx;p.y+=p.dy;
      if(p.x<0)p.x=c.width;if(p.x>c.width)p.x=0;
      if(p.y<0)p.y=c.height;if(p.y>c.height)p.y=0;
    }
    requestAnimationFrame(draw);
  }
  draw();
})();
