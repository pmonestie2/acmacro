constants
  retV=0
  retV1=0
  retV0=0
  _retV=0
  _retV1=0
  _retV2=0
  LOG_FILE_NAME=c:\MACROS\macro.log
  str1=a
  int1=0
  int2=0
  lastpos=none
  lastdest=none
  currentpos=none
  posTime = 0
  currentdest=none
  wasRelogin=0
  tStart=0
  tElapsed=0
  line=""
  rndChat="rnd_chat_not_found"
  allToons=lightje__lightt__lightbulb
  
end
constructs
  logfile=File
  cpos=List
  File1=File
  respList=List
  tmpList=List

end
listadd cpos, 50
listadd cpos,90
listadd cpos,130
listadd cpos,170
listadd cpos,196

//auto answers list for pre exit
ListAdd respList, Hey there!__gtg
ListAdd respList, Hello!__brb
ListAdd respList, Hi
ListAdd respList, Yo!
ListAdd respList, Hey!__How’s it going?
ListAdd respList, Hi.
ListAdd respList, Hello hello!
ListAdd respList, What’s up?
ListAdd respList, Hey__gtg in a sec.
ListAdd respList, Hiya!
ListAdd respList, Hi!__talk later.
ListAdd respList, Hey
ListAdd respList, Hola!__later
ListAdd respList, Sup!__bye
ListAdd respList, Hello!



call setpos none
procedure close1job
  call lclick 598, 269
  delay 500
  call lclick 543, 298
  delay 1 sec
end
procedure clearJob
  call lclick 208, 394
  call close1job
  call lclick 140, 265
  call close1job
  loop 5
    call lclick 193, 540
    call close1job
    call lclick 323, 591
  end
end

//RANDOM WALK ROOM
procedure charon1_2
  call sbreset 805890
  keydown a 4 sec
end
procedure charon2_3
  call sbreset 329497
  keydown a 4 sec
  keydown {LEFT} .2 sec
  keydown a 3 sec
end
procedure charon3_2
  call sbreset 589281
  keydown a 5 sec
end
procedure charon2_1
  call sbreset 93644
  keydown a 5 sec
  keydown {RIGHT} .5 sec
  keydown a 4 sec
end
procedure charon_space
  call sbreset 739690
  keydown a 3 sec
  call lclick 867, 95
  delay .4 sec
  call lclick 887, 95
  call transitionSpace
  call setpos pluto
end


procedure charon3_vendor
  call sbreset 772754
  keydown a 3 sec
  keydown {RIGHT} .2s
  call lclick 924,620
  delay .3 sec
  call lclick 952,653
  delay .3 sec
  call lclick 999,681
  call waitTrade
  call log tradeopen - will click
  delay .5 sec
  call log _end _delay
  call lclick 1000, 322
  delay .5 sec
  call lclick 1000, 322
  delay .5 sec
  call log allclicked
  loop 40
    call lclick 993, 718
    delay .1 sec
  end
  call dismissTrade
end
procedure earth1_2
  setconst retV=0
  call log earth1
  call sbreset 143648
  delay .2 sec
  call kdown a,5
end
procedure earth2_3
  call sbreset 478678
  call kdown a,4
  keydown {LEFT} 400
  call kdown a,3
end
procedure earth3_vendor
  call sbreset 411668
  call kdown a,3
  keydown {LEFT} 200
  loop 2
    call kdown a,.2
    delay .2 sec
    call lclick 423, 229
    call lclick 474, 186
    call lclick 508, 182
    call lclick 514, 206
    call lclick 506, 367
  end
  call waitTrade
  call sellall
  call dismissTrade
end


procedure earth2_j
  call sbreset 60293
  call kdown a,5
end
procedure earthj_2
  call sbreset 207543
  call kdown a,2
  keydown {LEFT} 400
  call kdown a,3
end
procedure earth3_2
  call sbreset 411668
  call kdown a,4
  keydown {LEFT} 600
  call kdown a,3
  keydown {LEFT} 300
  call kdown a,3
end
procedure earth2_1
  call sbreset 866689
  call kdown a,3
  keydown {RIGHT} 200
  call kdown a 3
end
procedure earth1_space
  call sbreset 904300
  call kdown a,1
  call lclick 1171, 105
  call transitionSpace
  setconst currentpos=earth
end




procedure sellAll
  loop 2
    loop 6
      call lclick 37, 558
    end
    loop 3
      call lsclick 62, 327
      call lsclick 138, 327
      call lsclick 214, 327
      
      call lsclick 62, 390
      call lsclick 138, 390
      call lsclick 214, 390
      
      call lsclick 62, 453
      call lsclick 138, 453
      call lsclick 214, 453
      
      call lsclick 62, 515
      call lsclick 138, 515
      call lsclick 214, 515
      loop 3
        call lclick 237, 556
      end
    end
  end
end
procedure waitTrade
  call log waittrade
  call waitObjVerb tradeOpen,  1092, 470
  call log waittrade1
  
  call lclick 507, 663
  call waitObjVerb trade2Open, 955, 223
  call log trade is open
end

procedure dismissTrade
  call lclick 1255, 253
  delay .5 sec
  call lclick 392, 626
  setconst retV=1
  while $retV=1
    call isObj tradeClose,1092, 472
  end
end

Object tradeOpen   // Test at coords   1092, 470
  92=0,0|150=2,0|122=4,0|52=6,0|33=8,0|86=10,0|
  49=0,2|77=2,2|150=4,2|163=6,2|147=8,2|162=10,2|
  49=0,4|49=2,4|91=4,4|182=6,4|231=8,4|162=10,4|
  57=0,6|88=2,6|183=4,6|174=6,6|137=8,6|173=10,6|
End Object

Object trade2open   // Test at coords  955, 223
  254=0,2|254=4,2|254=6,2|254=12,2|254=16,2|
  254=0,4|254=20,4|
  254=2,6|254=12,6|254=16,6|
End Object
Object tradeClose   // Test at coords 1092, 472
  49=0,0|78=2,0|150=4,0|160=6,0|145=8,0|162=10,0|
  49=0,2|49=2,2|92=4,2|183=6,2|231=8,2|162=10,2|
  57=0,4|89=2,4|184=4,4|174=6,4|137=8,4|173=10,4|
End
procedure closeJob
  call isObj jobOpen,  931, 575
  while $retV=1
    call lclick 1255, 254
    call yield 2
    call isObj jobOpen,  931, 575
  end
  delay 2 sec
end

Object jobOpen   // Test at coords 931, 575
  254=0,0|254=2,0|254=4,0|220=6,0|
  23=0,2|20=2,2|20=4,2|17=6,2|
  254=0,4|254=2,4|10=4,4|51=6,4|
  52=0,6|24=2,6|25=4,6|28=6,6|
  254=0,8|254=2,8|254=4,8|29=6,8|
  56=0,10|56=2,10|30=4,10|57=6,10|
End
procedure emptyOne using x,y
  mousepos $x,$y
  delay 100
  leftmousedown
  delay 200
  mousepos 99, 128
  delay 300
  leftmouseup
  delay 300
  call lclick 240, 165
  delay 300
end
procedure emptyHull
  keys m
  keys i
  loop 10
    call lclick 37, 553
  end
  call emptyOne 69, 324
  call emptyOne 132, 324
  call emptyOne 221, 324
  call emptyOne 69, 388
  call emptyOne 132, 388
  call emptyOne 221, 388
  call emptyOne 69, 450
  call emptyOne 132, 450
  call emptyOne 221, 450
  call emptyOne 69, 509
  call emptyOne 132, 509
  call emptyOne 221, 509
end

//NAVIGATION
procedure getMap
  keys u
  delay .1 sec
  keys m
  call isObj largeMap,707, 186
  if $retV=1
    call lclick 711, 187
    delay .2 sec
  end
  keydown - 3 sec
  delay .3 sec
  loop 2
    mousepos 396,397
    delay .2 sec
    leftmousedown
    mousepos 396,600
    delay .2 sec
    leftmouseup
  end
  loop 2
    mousepos 90,416
    delay .2 sec
    leftmousedown
    mousepos 400,416
    delay .2 sec
    leftmouseup
  end
  delay .2 sec
  
end
Object largeMap   // Test at coords 707, 186
  91=0,0|90=2,0|90=4,0|
  227=0,2|227=2,2|227=4,2|
  190=0,4|190=2,4|190=4,4|
  93=0,6|93=2,6|92=4,6|
End
procedure closeMap
  keys i
  delay 100
  keys m
  delay 100
  keys m
  delay 100
End
procedure transitionSpace
  call log transition_space
  call waitObjMax waitSector, 297, 142,15
  call waitObj inventory,35, 685
  delay 7 sec
end
procedure hasDamage
  setconst retV=0
  isred 796, 687
    exit
  end
  isred 791, 687
    exit
  end
  isRed 780, 686
    exit
  end
  isred 789, 684
    exit
  end
  isred 779, 685
    exit
  end
end
procedure gate using x,y,noGate
  while 1=1
    call log trying gate $x $y $currentpos
    call gateInternal $x, $y, $noGate
    if $retV=1
      call log gate is done $x $y
      exit
    end
    call log gate failed doing again $x $y
  end
end
procedure gateInternal using x,y,noGate
  keys `
  delay 200
  call log gate::$x -- $y
  call getMap
  delay .2 sec
  call lclick $x,$y
  delay 2 sec
  delay 1 sec
  setconst retV1=0
  call isobj gate,1227, 508
  if $retV=1
    setconst retV1=1
  end
  call isobj station,1224, 506
  if $retV=1
    call log station found
    setconst retV1=1
  end
  if $retV1=0
    call log waiting for igate obj
    call waitObjForGate igate,1230, 517,istation,1221, 506
    if $retV=0
      exit
    end
    delay .1 sec
    keys q
  end
  call log waiting for station
  call waitObjForGate gate,1227, 508,station,1224, 506
  if $retV=0
    exit
  end
  delay 1 sec
  if $noGate=1
  else
    call lclick 1236, 515 //Gate button
    call transitionSpace
  end
  setconst retV=1
end
procedure waitObjForGate using o,x,y,o1,x1,y1
  setconst retV=0
  setconst tStart=0
  setconst tElapsed = 0
  compute tStart={ElapsedMSec}
  compute int1=$x+100
  compute int2=$y+100
  mousepos $int1,$int2
  while $retV=0
    isobject $o at $x,$y
      setconst retV=1
    end
    isobject $o1 at $x1,$y1
      setconst retV=1
    end
    call yield 2
    if $retV=1
      exit
    end
    compute tElapsed = {ElapsedMSec} - $tStart
    if $tElapsed>160000
      call log elapsed $tElapsed - exiting
      //if $int2>30000
      exit
    end
  end
end


Object igate   // Test at coords 1230, 517
  32=0,0|31=2,0|26=4,0|71=6,0|53=8,0|30=10,0|32=12,0|
  31=0,2|31=2,2|80=4,2|88=6,2|80=8,2|61=10,2|27=12,2|
  31=0,4|60=2,4|54=4,4|58=6,4|58=8,4|48=10,4|48=12,4|
  31=0,6|32=2,6|32=4,6|72=6,6|50=8,6|32=10,6|31=12,6|
End
Object gate   // Test at coords 1227, 508
  222=0,0|171=2,0|84=4,0|227=6,0|112=8,0|85=10,0|
  77=0,2|233=2,2|255=4,2|254=6,2|97=8,2|85=10,2|
  127=0,4|250=2,4|239=4,4|244=6,4|93=8,4|86=10,4|
  71=0,6|71=2,6|69=4,6|67=6,6|68=8,6|89=10,6|
  93=0,8|93=2,8|90=4,8|87=6,8|74=8,8|170=10,8|
End
Object istation   // Test at coords 1221, 506
  18=0,0|20=2,0|32=4,0|78=6,0|89=8,0|88=10,0|
  22=0,2|19=2,2|34=4,2|70=6,2|85=8,2|73=10,2|
  20=0,4|28=2,4|68=4,4|88=6,4|60=8,4|26=10,4|
  21=0,6|49=2,6|87=4,6|76=6,6|26=8,6|32=10,6|
  22=0,8|68=2,8|88=4,8|66=6,8|32=8,8|31=10,8|
End
Object station   // Test at coords 1224, 506
  59=0,0|156=2,0|238=4,0|252=6,0|
  69=0,2|145=2,2|221=4,2|225=6,2|
  132=0,4|223=2,4|212=4,4|87=6,4|
  193=0,6|235=2,6|88=4,6|82=6,6|
  228=0,8|221=2,8|77=4,8|90=6,8|
End
Object waitSector   // Test at coords 297, 142
  37=0,0|37=2,0|37=4,0|37=6,0|
  35=0,2|35=2,2|35=4,2|35=6,2|
  36=0,4|42=2,4|52=4,4|72=6,4|
  79=0,6|77=2,6|79=4,6|78=6,6|
  76=0,8|76=2,8|78=4,8|78=6,8|
End

Object inventory   // Test at coords 35, 685
  78=0,0|82=2,0|85=4,0|86=6,0|89=8,0|
  86=0,2|74=2,2|65=4,2|131=6,2|87=8,2|
  84=0,4|69=2,4|75=4,4|178=6,4|135=8,4|
  78=0,6|69=2,6|118=4,6|119=6,6|103=8,6|
  74=0,8|90=2,8|233=4,8|201=6,8|181=8,8|
End
procedure setupKranken
  keys m
  keys i
  mousepos 642, 584
  delay .3 sec
  leftmousedown
  delay .3 sec
  mousepos 788, 719
  delay .3 sec
  leftmouseup
  delay .3 sec
  keys `
  delay .5 sec
  keys 4
  delay 1 sec
  keys 1
end
procedure gateSat using x,y
  call log gate::$x -- $y
  call getMap
  call lclick $x,$y
  delay 3 sec
  setconst retV1=0
  call isobj sat,1223, 510
  if $retV=1
    setconst retV1=1
  end
  if $retV1=0
    call log waiting for isat obj
    call waitObjMax isat,1229, 509,12
    if $retV=0
      exit
    else
      keys q
    end
  end
  call log waiting for sat
  call waitObj sat,1225, 512,1
  delay 1 sec
  keydown a 3 sec
  loop 7
    call lclick 1238, 516
    delay 1 sec
  end
end

Object isat   // Test at coords 1229, 509
  62=0,0|32=2,0|64=4,0|44=6,0|66=8,0|46=10,0|
  60=0,2|48=2,2|68=4,2|50=6,2|66=8,2|60=10,2|
  77=0,4|43=2,4|42=4,4|44=6,4|55=8,4|64=10,4|
  44=0,6|35=2,6|24=4,6|25=6,6|29=8,6|29=10,6|
End
Object sat   // Test at coords 1225, 512
  139=0,0|190=2,0|205=4,0|147=6,0|184=8,0|
  173=0,2|96=2,2|199=4,2|96=6,2|74=8,2|
  69=0,4|187=2,4|64=4,4|73=6,4|81=8,4|
  70=0,6|168=2,6|65=4,6|92=6,6|90=8,6|
End

procedure checkNavProgress
  call log checking status $lastpos $currentpos
  if $lastpos=none or $lastpos<>$currentpos or $lastdest<>$currentdest
    setconst lastpos=$currentpos
    setconst lastdest=$currentdest
    if $lastpos=none
      setconst lastpos=under
      setconst currentpos=under
      setconst lastdest=under
    end
    call log checked status $lastpos $currentpos
    exit
  end
  if $lastpos=$currentpos and $lastdest=$currentdest
    call log will relogin as pos didnot change:$lastpos  $currentpos  $lastdest $currentdest
    call relogin
    if $currentpos<>earthS and $currentpos<>charonS
      keys `
      keys n
      delay .3 sec
      keys n
      delay .3 sec
      keys q
      delay 2 min
    end
    restart
  end
end
procedure killenb
  execprogram taskkill /F /IM net7proxy.exe
  execprogram taskkill /F /IM client.exe
  execprogram taskkill /F /IM LaunchNet7.exe
  delay 3 sec
end
procedure launchEnb
  ExecProgram "C:\MACROS\startenb.bat"
end
procedure relogin
  setconst int1=0
  setconst retV1=0
  say /quit
  delay 3 sec
  while $retV1=0
    inc int1
    call log logging attempt:$int1
    call killenb
    call launchEnb
    delay 10 sec
    SetActiveWindow Earth & Beyond
    Keys {SPACE}
    Delay 12 sec
    SetActiveWindow Earth & Beyond
    keys {SPACE}
    delay 7 sec
    keys $PASSWORD
    keys {RETURN}
    delay 15 sec
    call lclick 220,cpos[$CHAR_SLOT]
    delay 7 sec
    call lclick 1207, 713
    delay 30 sec
    call conlog testing inv icon
    call isobj inventory,35, 685
    if $retV=1
      setconst retV1=1
    end
    call isobj inventory,35, 685
    
    if $retV=1
      call initReadChat2
      setconst retV1=1
    end
    
  end
  delay 5 sec
  //In case we were dead
  keys `
  delay 3 sec
  call lclick 646, 686
  delay 3 sec
  call isobj towOn,319, 490
  if $retV=1
    call lclick 319, 490
    delay 8 sec
    say /quit
    execprogram taskkill /F /IM net7proxy.exe
    execprogram taskkill /F /IM client.exe
    execprogram taskkill /F /IM LaunchNet7.exe
    restart
  end
  setconst wasRelogin=1
end
Object towOn   // Test at coords 319, 490
  96=0,0|96=2,0|254=6,0|97=10,0|98=18,0|98=22,0|
  84=0,2|84=2,2|254=6,2|254=12,2|254=16,2|
  65=0,4|66=2,4|66=4,4|254=6,4|66=8,4|67=14,4|254=22,4|
End


procedure earth_uranus
  call log navigate earth->uranus $currentpos
  if $currentpos=earth
    call earth_aba
  end
  if $currentpos=aba
    call aba_saturn
  end
  if $currentpos=saturn
    call saturn_uranus
  end
end
procedure earth_uranus_je
  call log navigate earth->uranus_je $currentpos
  if $currentpos=earth
    call earth_jupiter_je
  end
  if $currentpos=jupiter
    call jupiter_saturn
  end
  if $currentpos=saturn
    call saturn_uranus
  end
end




procedure earth_pluto
  call log navigate earth->pluto cpos=$currentpos
  if $currentpos=earth
    call earth_aba
  end
  if $currentpos=aba
    call aba_saturn
  end
  if $currentpos=saturn
    call saturn_akeron
  end
  if $currentpos=akeron
    call akeron_pluto
  end
end
procedure pluto_earth
  call log navigate pluto->earth currentpos=$currentpos
  if $currentpos=pluto
    call pluto_akeron
  end
  if $currentpos=akeron
    call akeron_saturn
  end
  if $currentpos=saturn
    call saturn_aba
  end
  if $currentpos=aba
    call aba_earth
  end
  call log found none
end
procedure pluto_charonS using noGate
  if $noGate=1
    call gate 418, 576, 1
  else
    call gate 418, 576
    call setpos charonS
  end
end
procedure pluto_akeron
  mousepos 405, 900
  delay 1 sec
  call log put mouse under
  call gate 405, 617
  call setpos akeron
end
procedure akeron_saturn
  call gate 335, 548
  call setpos saturn
end
procedure akeron_pluto
  call gate 387, 530
  call setpos pluto
end
procedure jupiter_saturn
  call gate 403, 619
  call setpos saturn
end
procedure saturn_akeron
  call gate 429, 568
  call setpos akeron
end
procedure saturn_uranus
  call gate 380, 576
  call setpos uranus
end
procedure uranus_pluto
  call gate 386, 527
  call setpos pluto
end
procedure saturn_aba
  call gate 379, 588
  call setpos aba
end
procedure aba_saturn
  call gate 446, 560
  call setpos saturn
end
procedure aba_earth
  call gate 281, 497
  call setpos earth
end
procedure earth_aba
  call log earth_aba gate
  call gate 327, 490
  call setpos aba
end
procedure earth_earths
  call gate 362, 517
  call setpos earthS
end
procedure earth_jupiter_je
  keys i
  delay 200
  keys u
  delay 200
  call lclick 74, 192
  mousepos 323, 445
  delay 100
  loop 30
    leftclick
    delay 150
  end
  loop 4
    call lclick 321, 606
    delay 400
  end
  mousepos 85, 548
  delay 200
  leftmousedown
  delay 200
  mousepos 867, 725
  delay 500
  leftmouseup
  delay 2 sec
  keys 5
  delay 500
  call waitobj wormhole, 394, 353
  call lclick 550, 424
  call transitionSpace
  call setpos jupiter
end
Object wormhole   // Test at coords 394, 353
  61=0,0|254=4,0|118=6,0|135=8,0|254=10,0|
  64=0,2|220=2,2|152=4,2|64=6,2|30=8,2|254=10,2|
End

procedure earthGetPos
  call getMap
  call isObj charonS, 290, 544
  if $retV=1
    call log charonS found
    setconst currentpos=charonS
    exit
  end
  call isObj charonS1,258, 546
  if $retV=1
    call log charonS found
    setconst currentpos=charonS
    exit
  end
  
  call isObj earthS, 207, 540
  if $retV=1
    setconst currentpos=earthS
    exit
  end
  call isObj jupiter, 235, 360
  if $retV=1
    setconst currentpos=jupiter
    exit
  end
  
  call isObj saturn, 237, 359
  if $retV=1
    setconst currentpos=saturn
    exit
  end
  call isObj akeron, 208, 361
  if $retV=1
    setconst currentpos=akeron
    exit
  end
  call isObj aba,286, 362
  if $retV=1
    setconst currentpos=aba
    exit
  end
  call isObj uranus, 234, 360
  if $retV=1
    setconst currentpos=uranus
    exit
  end
  call isObj earth, 238, 363
  if $retV=1
    setconst currentpos=earth
    exit
  end
  call isObj pluto,  195, 361
  if $retV=1
    setconst currentpos=pluto
    exit
  end
  
  
end

//SECTOR DETECT
Object jupiter   // Test at coords 235, 360
  254=6,2|254=16,2|254=18,2|
  254=6,4|254=14,4|254=20,4|
  254=6,6|254=14,6|254=20,6|
End
Object charonS   // Test at coords 290, 544
  254=2,2|254=4,2|254=10,2|254=16,2|
  254=2,4|254=16,4|
End

Object charonS1   // Test at coords 258, 546
  254=12,0|254=18,0|
  254=12,2|
  254=4,4|254=12,4|
  254=6,6|254=8,6|254=12,6|254=18,6|
End

Object pluto   // Test at coords 195, 361
  254=2,0|254=10,0|
  254=2,2|254=10,2|
  254=2,4|254=4,4|254=10,4|
End
Object saturn   // Test at coords  237, 359254=2,0|
  254=0,2|
  254=2,4|254=12,4|
  254=4,6|254=8,6|254=10,6|254=12,6|
  254=4,8|254=12,8|
End
Object uranus   // Test at coords 234, 360
  254=2,0|254=8,0|
  254=2,2|254=8,2|254=18,2|254=20,2|
  254=2,4|254=8,4|
  254=2,6|254=8,6|254=16,6|
End
Object akeron   // Test at coords 207, 362
  254=6,2|254=14,2|254=20,2|
  254=2,4|254=14,4|254=20,4|254=22,4|
  254=8,6|254=16,6|254=20,6|
  
End
Object aba   // Test at coords 286, 362
  254=4,0|254=6,0|254=12,0|254=18,0|
  254=12,2|
  254=2,4|254=8,4|254=12,4|
End
Object earth   // Test at coords 238, 363
  254=4,0|254=6,0|254=20,0|
  254=12,2|254=14,2|
End
Object earthS   // Test at coords 207, 540
  254=6,4|
  254=6,6|254=14,6|254=16,6|254=22,6|254=24,6|
  254=6,8|254=18,8|254=22,8|
End
//USEFULL STUFF

procedure waitObjFast using o,x,y,t
  setconst retV=0
  while $retV=0
    isobject $o at $x,$y
      setconst retV=1
    end
    call yield $t
  end
end
procedure waitObjMax using o,x,y,max
  setconst retV=0
  compute int1=$x+100
  compute int2=$y+200
  mousepos $int1,$int2
  loop $max
    isobject $o at $x,$y
      setconst retV=1
      exit
    end
    call yield 2
  end
end


procedure waitObjVerb using o,x,y,c,damage
  setconst retV=0
  compute int1=$x+100
  compute int2=$y+200
  mousepos $int1,$int2
  while $retV=0
    call log checking obj $o
    isobject $o at $x,$y
      setconst retV=1
      call yield .2
      call log found obj $o
      
      exit
    end
    call log yielding some, 2 _sec
    call yield 2
    if $c=1
      keydown a 2 sec
    end
    if $damage=1
      call hasDamage
    end
  end
  call log found damage, exiting proc
  setconst retV=0
end

procedure waitObj using o,x,y,c,damage
  setconst retV=0
  compute int1=$x+100
  compute int2=$y+200
  mousepos $int1,$int2
  while $retV=0
    isobject $o at $x,$y
      setconst retV=1
      call yield .2
      exit
    end
    call yield 2
    if $c=1
      keydown a 2 sec
    end
    if $damage=1
      call hasDamage
    end
  end
  call log found damage, exiting proc
  setconst retV=0
end

procedure ran
  loop 100
    Compute int1 = Ceil(Random(0)*1000000)
    call conlog $int1
    call sbreset $int1
    delay 1 sec
  end
end

procedure sbreset using point
  setmemory 00BDBB80, 0
  setmemory 00BDB1C0, $point
  call log resetting to $point
  say /starbase reset
  delay 1 sec
end
procedure log using x
  FormatDateTime str1 = {now}, m/d/yy hh:nn:ss
  fileopen logfile,$LOG_FILE_NAME
  fileappend logfile
  filewrite logfile,[$str1] - $x
  fileclose logfile
  timestamp $x
end
procedure conlog using x
  //  say /t noobnoobje $x
  call log $x
end

procedure kdown using k,t
  keydown $k $t sec
  delay .4 sec
end
procedure yield using x
  delay $x sec
  processMessages
end
procedure isobjFast using o,x,y
  setconst retV=0
  loop 2
    isobject $o at $x,$y
      call log FOUND OBJ $o AFTER {LOOPNO} ITERATIONS
      call yield .2
      setconst retV=1
      exit
    end
    call yield .2
  end
  call log Not found obj $o
end

procedure isobj using o,x,y
  setconst retV=0
  loop 4
    isobject $o at $x,$y
      call log FOUND OBJ $o AFTER {LOOPNO} ITERATIONS
      call yield .1
      setconst retV=1
      exit
    end
    delay 300
  end
  call yield .2
  call log Not found obj $o
end
procedure lsclick using x,y
  mousepos $x,$y
  delay .1 sec
  leftclick shift
  delay .1 sec
end
procedure lclick using x,y,click
  mousepos $x,$y
  delay .2 sec
  if $click=0
  else
    leftclick
  end
end
procedure setpos using cpos
  setconst currentpos=$cpos
  setconst posTime = {ElapsedMSec}
  call log we are in $cpos 
end


procedure rnum using x
  compute int1=random(12)* $x +1
  compute retV=floor($int1)
end  

procedure respondExit
  ListCount respList, int1
  call rnum $int1
  setconst retV=respList[$retV]
  RegExSplit tmpList, <<__>>, $retV
  ListCount tmpList,int1
  loop $int1
    setconst retV=tmpList[{LOOPNO}]
    keys r
    delay 1 sec
    keys $retV {RETURN}
    timestamp respoding: $retV
    delay 5 sec
  end
  RegExSplit tmpList, <<__>>, $allToons
  ListCount tmpList,int1
  loop $int1
    setconst retV=tmpList[{LOOPNO}]
    timestamp pinging alts $retV
    keys /t $retV hi {RETURN}
  end
end


procedure initReadChat
  //execprogram C:\MACROS\startlogpoll.bat
  //delay 4 sec
  SetActiveWindow Earth & Beyond
  delay 3 sec
  compute rndChat = trunc(rnd(12321312))
  setconst rndChat=hello_$rndChat
  timestamp RND_CHAT= $rndChat
  SetActiveWindow Earth & Beyond
  delay 3 sec
  say /t $toonName $rndChat
end


procedure readChat using beginPos,sFind,lPos
  delay 2 sec
  timestamp reading log file
  FileOpen File1, C:\MACROS\chat.log
  FileReset File1
  setconst beginPos=0
  while 1 = 1
    FileRead File1, line
    inc beginPos
    StrPos sFind = $rndChat, $line
    if $sFind>0
      timestamp found start search pos $beginPos for $rndChat
      break
    end
    if {EOF} = True
      Break
    end
  end
  timestamp file size $beginPos
  FileReset File1
  setconst lpos=0
  while 1 = 1
    FileRead File1, line
    inc lpos
    if $lpos > $beginPos
	  timestamp checking at pos: $lpos
      StrPos sFind = (private), $line
      if $sFind > 0
        timestamp FOUND_IT   at pos: $lpos - $line -- RESPOND AND EXIT!!
        call respondExit
        timestamp _EXIT_MACRO FOUND $line
        call killenb
        stop
      end
    end
    if {EOF} = True
      Break
    end
  end
  FileClose File1
  timestamp END reading log file
end




