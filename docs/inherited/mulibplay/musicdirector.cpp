/**************************************
 *  mulibplay - Music Library Player  *
 *  Copyright (c) 2020 by Mike Inman  *
 * aka MangoCats, all rights reserved *
 *   Free, Open Source, MIT license   *
 **************************************/

#include "musicdirector.h"
#include "global.h"

MusicDirector::MusicDirector( Playout *pop ) : QObject(pop),
               po(pop), breatheInterval(3), repeatCount(0),
               minLength(30), maxLength(3600), maxDepth(10800), sampleRate(44100),
               minWeightLimit(0.001), kidSongWeight(0.000001), mrpInitialized(false), selectingCuts(false)
{ restoreSettings();
  connect( po, SIGNAL( needCuts(qint32)), SLOT(selectCuts(qint32))   );
  connect( po, SIGNAL(cutPlayed(qint32)), SLOT( cutPlayed(qint32))   );
  connect( po, SIGNAL(firstCutPlaying()), SLOT(initMostRecentPlay()) );
  selectStationByTime();
  autoSelectStation = true;
}

void MusicDirector::restoreSettings()
{ QSettings *sp = new QSettings();
  readStationDescriptions();
/*
  trackId          ->setValue( sp->value( "musicDirectorTrackId", 0     ).toInt()    );
  showTracks     ->setChecked( sp->value( "showTracks"          , false ).toBool()   );
  limitCuts      ->setChecked( sp->value( "limitCuts"           , false ).toBool()   );
  persistOnChange->setChecked( sp->value( "persistOnChange"     , true  ).toBool()   );
  minCutsAtOnce    ->setValue( sp->value( "minCutsAtOnce"       , 1     ).toInt()    );
*/
              minLength = sp->value( "minLength"           ,       30 ).toInt();  // If cut is less than 30 seconds long, don't consider it
              maxLength = sp->value( "maxLength"           ,     3600 ).toInt();  // If cut is more than 1 hour long, don't consider it
               maxDepth = sp->value( "maxDepth"            ,    10800 ).toInt();  // If cut starts more than 3 hours into album, don't consider it
        breatheInterval = sp->value( "breatheInterval"     ,        3 ).toInt();
             exclPoolSz = sp->value( "exclPoolSz"          ,     1000 ).toInt();
             randPoolSz = sp->value( "randPoolSz"          ,      100 ).toInt();
               maxSeeds = sp->value( "maxSeeds"            ,        5 ).toInt();
         minWeightLimit = sp->value( "minWeightLimit"      ,    0.001 ).toReal();
          kidSongWeight = sp->value( "kidSongWeight"       , 0.000001 ).toReal();
//  setStationDescriptions( sp->value( "stationDescriptions"   ).toStringList()   );
           selectStation( sp->value( "stationName"         , ""       ).toString() );
           setCoreTracks( sp->value( "coreTracksList"                 ).toList()   );
  sp->deleteLater();
}

void MusicDirector::saveSettings()
{ QSettings *sp = new QSettings();
/*
  sp->setValue( "musicDirectorTrackId", trackId         );
  sp->setValue( "showTracks"          , showTracks      );
  sp->setValue( "limitCuts"           , limitCuts       );
  sp->setValue( "persistOnChange"     , persistOnChange );
  sp->setValue( "minCutsAtOnce"       , minCutsAtOnce   );
*/
  sp->setValue( "minLength"           , minLength                );
  sp->setValue( "maxLength"           , maxLength                );
  sp->setValue( "maxDepth"            , maxDepth                 );
  sp->setValue( "breatheInterval"     , breatheInterval          );
  sp->setValue( "exclPoolSz"          , exclPoolSz               );
  sp->setValue( "randPoolSz"          , randPoolSz               );
  sp->setValue( "maxSeeds"            , maxSeeds                 );
  sp->setValue( "minWeightLimit"      , minWeightLimit           );
  sp->setValue( "kidSongWeight"       , kidSongWeight            );
  sp->setValue( "stationName"         , stationName              );
  sp->setValue( "coreTracksList"      , getCoreTracksVar()       );
//  sp->setValue( "stationDescriptions" , getStationDescriptions() );
  sp->deleteLater();
}

void MusicDirector::breathe()
{ if ( repeatCount++ > breatheInterval )
    { emit takeABreath();
      QCoreApplication::processEvents( QEventLoop::AllEvents, 5 );
      repeatCount = 0;
    }
}

/**
 * @brief MusicDirector::cutPlayed - enter cutId in the playHistory
 * @param cutId
 */
void MusicDirector::cutPlayed( qint32 cutId )
{ qint32 trackId = de.cutTrackId( cutId );
  if ( trackId < 1 ) return;
  qint64 time = QDateTime::currentSecsSinceEpoch();
  markCutPlayed( cutId, time );
  QSqlQuery query(da->db);

  query.prepare( "SELECT mbid FROM tracks WHERE trackId=:id" );
  query.bindValue( ":id", trackId );
  if ( !query.exec()  ) return;
  if ( !query.first() ) return;
  QString trackMbid = query.value( "mbid" ).toString();

  query.prepare( "INSERT INTO playHistory ( time, cutId, mbid ) VALUES ( :time, :cid, :id );" );
  query.bindValue( ":time", time );
  query.bindValue( ":cid" , cutId );
  query.bindValue( ":id"  , trackMbid );
  if ( !query.exec() )
    { qDebug( "cutPlayed() ERROR: %s, %s", qPrintable( query.lastError().text() ), qPrintable( query.executedQuery() ) ); return; }
}

/**
 * @brief MusicDirector::readStationDescriptions - from the database
 */
void MusicDirector::readStationDescriptions()
{ stationDescriptions.clear();
  QSqlQuery query(da->db);
  query.prepare( "SELECT * FROM programs;" );
  if ( !query.exec() ) { qDebug( "ERROR: %s, %s", qPrintable( query.lastError().text() ), qPrintable( query.executedQuery() ) ); return; }
  while ( query.next() )
    { QString name = query.value( "name" ).toString();
      if ( name.size() > 0 )
        { QStringList tids = query.value( "trackList" ).toString().split( QChar('\n'), QString::SkipEmptyParts );
          if ( tids.size() > 0 )
            { QList<qint32> tidl;
              foreach ( QString tid, tids )
                { qint32 tn = Database::idFromLine( tid );
                  if ( tn > 0 )
                    tidl.append(tn);
                }
              if (( tidl.size() > 0 ) && ( !stationDescriptions.contains(name) ))
                stationDescriptions.insert( name, tidl );
            }
        }
    }
}

void MusicDirector::selectStationByTime()
{ QSqlQuery query(da->db);
  query.prepare( "SELECT * FROM programs;" );
  if ( !query.exec() ) { qDebug( "ERROR: %s, %s", qPrintable( query.lastError().text() ), qPrintable( query.executedQuery() ) ); return; }
  QMap<QString,qint32> times;
  QList<QString> names;
  while ( query.next() )
    { QString name = query.value( "name" ).toString();
      QString time = query.value( "startTime" ).toString();
      if (( name.size() < 1 ) || ( time.size() != 5 ))
        qDebug( "MusicDirector::selectStationByTime() name %s time %s", qPrintable(name), qPrintable(time) );
       else
        { if ( time.at(2) == QChar(':') )
            { bool okh = false;
              bool okm = false;
              qint32 tv = time.mid(0,2).toInt( &okh ) * 60 + time.mid(3,2).toInt( &okm );
              if ( okh && okm )
                { times.insert( name, tv );
                  names.append( name );
                }
            }
        }
    }
  if ( names.size() > 0 )
    { if ( names.size() == 1 )
        selectStation( names.at(0) );
       else
        { QTime curTime = QDateTime::currentDateTime().time();
          qint32 tNow = curTime.minute() + 60 * curTime.hour();
          // Find the station time that is closest to tNow in the past
          qint32 minDelta = 24*60+1;
          QString selStation = "";
          foreach ( QString sn, names )
            { qint32 deltaMinutes = tNow - times.value(sn);
              if ( deltaMinutes < 0 )
                deltaMinutes += 24*60;
              if ( deltaMinutes < minDelta )
                { selStation = sn;
                  minDelta = deltaMinutes;
                }
            }
          if ( selStation.size() > 0 )
            selectStation( selStation );
        }
    }
}


/**
 * @brief MusicDirector::setStationDescriptions - parse a StringList into the live structure
 * @param sdl - list of individual station descriptions as: name|track1,track2,...trackN
 */
void MusicDirector::setStationDescriptions( QStringList sdl )
{ stationDescriptions.clear();
  foreach( QString desc, sdl )
    if ( desc.contains( QChar('|') ) )
      { int divi = desc.indexOf( QChar('|') );
        QString name = desc.mid( 0, divi );
        QStringList tids = desc.mid( divi+1 ).split( QChar(','), QString::SkipEmptyParts );
        if (( name.size() > 0 ) && ( tids.size() > 0 ) && ( !stationDescriptions.contains(name) ))
          { QList<qint32> tidl;
            foreach( QString tid, tids )
              tidl.append( tid.toInt() );
            stationDescriptions.insert( name, tidl );
          }
      }
}

/**
 * @brief MusicDirector::getStationDescriptions - convert the live structure to a StringList
 * @return list of individual station descriptions as: name|track1,track2,...trackN
 */
QStringList MusicDirector::getStationDescriptions()
{ QStringList descList;
  QStringList nameList = stationDescriptions.keys();
  foreach( QString name, nameList )
    { QString desc = name + "|";
      QList<qint32> tids = stationDescriptions.value(name);
      foreach( qint32 tid, tids )
        desc.append( QString( "%1,").arg(tid) );
      desc.chop(1);
      descList.append(desc);
    }
  return descList;
}

/**
 * @brief MusicDirector::selectStation - setCoreTracks to those defined for name
 * @param name - name of station to select
 */
void  MusicDirector::selectStation( QString name )
{ if ( stationDescriptions.contains(name) )
    { setCoreTracks( stationDescriptions.value( name ) );
      stationName = name;
      autoSelectStation = false;
    }
   else
    qDebug( "Unknown station '%s' attempted to be selected.", qPrintable(name) );
}

/**
 * @brief MusicDirector::setCoreTracks
 * @param trackIdList - directly usable list of tracks (as stored in stationDescriptions)
 */
void MusicDirector::setCoreTracks( QList<qint32>trackIdList )
{ coreTracks = trackIdList; }

/**
 * @brief MusicDirector::setCoreTracks
 * @param trackIdList - list of variants (as stored in QSettings)
 */
void MusicDirector::setCoreTracks( QList<QVariant>trackIdList )
{ coreTracks.clear();
  foreach( QVariant trackId, trackIdList )
    coreTracks.append( trackId.toInt() );
}

/**
 * @brief MusicDirector::getCoreTracksVar
 * @return list of variants (as stored in QSettings)
 */
QList<QVariant> MusicDirector::getCoreTracksVar()
{ QList<QVariant> vtl;
  foreach( qint32 t, coreTracks )
    vtl.append( QVariant( t ) );
  return vtl;
}

/**
 * @brief MusicDirector::rotationValueToSeconds
 * @param rv - rotation value as is stored in the database
 * @return number of seconds this value translates to (0.0 = 1 hour, 1.0 = 10 hours, -1.0 = 6 minutes)
 */
qint64  MusicDirector::rotationValueToSeconds( qreal rv )
{ return (qint64)(pow(10.0,rv) * 3600.0); }

QString  MusicDirector::rotationValueToDesc( qreal rv )
{ return timeRemainDesc( rotationValueToSeconds( rv ) ); }

QString MusicDirector::timeRemainDesc( qint64 sec )
{ if ( sec < 60 )      return QString( "%1sec" ).arg(        sec                  , 5         );
  if ( sec < 3600 )    return QString( "%1min" ).arg( ((qreal)sec)/   60.0        , 5, 'f', 1 );
  if ( sec < 3600*24 ) return QString( "%1hrs" ).arg( ((qreal)sec)/ 3600.0        , 5, 'f', 1 );
   else                return QString( "%1day" ).arg( ((qreal)sec)/(3600.0 * 24.0), 5, 'f', 1 );
}

/**
 * @brief MusicDirector::recoveryWeight
 * @param age - time since track was played, in seconds
 * @param rotSecs - track's rotation value translated to seconds
 * @param recSecs - track's recovery value translated to seconds
 * @return weight (probability to be selected for play) to apply to a track
 */
qreal MusicDirector::recoveryWeight( qint64 age, qint64 rotSecs, qint64 recSecs )
{ if ( age <=  rotSecs            ) return 0.0;
  if ( age >= (rotSecs + recSecs) ) return 1.0;
  return ((qreal)(age - rotSecs)) / ((qreal)recSecs);
}

/**
 * @brief MusicDirector::initMostRecentPlay
 *   builds the artists and tracks most recent play structures from the database
 *   typically called once at program start then the structures are updated as each
 *   new track finishes playing (or is put in the play queue).
 */
void MusicDirector::initMostRecentPlay()
{ artistsMostRecentPlay.clear();
   tracksMostRecentPlay.clear();
  QSqlQuery query(da->db);
  query.prepare( "SELECT * FROM playHistory;" );
  if ( !query.exec() ) { qDebug( "ERROR: %s, %s", qPrintable( query.lastError().text() ), qPrintable( query.executedQuery() ) ); return; }
  while ( query.next() )
    { bool ok = false;
      qint32 cutId = query.value( "cutId" ).toInt( &ok );
      if ( ok )
        { qint64 playTime = query.value( "time" ).toLongLong( &ok );
          if ( ok )
            { markCutPlayed( cutId, playTime );
              breathe();
            }
        }
    }
  // also insert the tracks/artists in the playout queue as being played now (will be replaced when they actually finish playing)
  if ( po != nullptr )
    foreach ( Cut *cp, po->cutQueue )
      if ( cp != nullptr )
        markCutPlayed( cp->getId() );
  qDebug( "most recent %d artists %d tracks", artistsMostRecentPlay.size(), tracksMostRecentPlay.size() );
  mrpInitialized = true;
}

/**
 * @brief MusicDirector::markCutPlayed - note cut as played in the tracks and artists recent lists
 * @param cutId
 * @param playTime - defaults to current time if not specified
 */
void MusicDirector::markCutPlayed( qint32 cutId, qint64 playTime )
{ if ( playTime < 1 )
    playTime = QDateTime::currentSecsSinceEpoch();

  qint32 trackId = de.cutTrackId( cutId );
  if ( trackId > 0 )
    { if ( !tracksMostRecentPlay.contains( trackId ) )
        tracksMostRecentPlay.insert( trackId, playTime );
       else if ( tracksMostRecentPlay.value( trackId ) < playTime )
        tracksMostRecentPlay.insert( trackId, playTime );
    }
  qint32 artistId = de.cutArtistId( cutId );
  if ( artistId > 0 )
    { if ( !artistsMostRecentPlay.contains( artistId ) )
        artistsMostRecentPlay.insert( artistId, playTime );
       else if ( artistsMostRecentPlay.value( artistId ) < playTime )
        artistsMostRecentPlay.insert( artistId, playTime );
    }
}

/**
 * @brief MusicDirector::initEligibleArtists - based on the most recent play structure
 *   and current time, determine which artists are eligible for play.
 *   TODO: consider the future - estimate what the list will look like when the track
 *     to be selected will actually be played.
 * @return true if successful
 */
bool MusicDirector::initEligibleArtists()
{ if ( !mrpInitialized )
    return false;
  eligibleArtists.clear();
  qint64 now = QDateTime::currentSecsSinceEpoch();
  QSqlQuery query(da->db);
  query.prepare( "SELECT * FROM artists;" );
  if ( !query.exec() ) { qDebug( "ERROR: %s, %s", qPrintable( query.lastError().text() ), qPrintable( query.executedQuery() ) ); return false; }
  while ( query.next() ) // Start with a map of all artists
    { bool ok = false;
      qint32 artistId = query.value( "artistId" ).toInt( &ok );
      if ( ok )
        { qreal restraint = query.value( "restraint" ).toReal( &ok );
          if ( !ok ) restraint = DEF_RESTRAINT;
          qreal weight = pow(10.0,-restraint);

          qreal rotation = query.value( "rotation" ).toReal( &ok );
          if ( !ok ) rotation = DEF_ROTATION_ART;
          qint64 rotSecs = rotationValueToSeconds( rotation );
          bool rotationBlock = false;
          if ( artistsMostRecentPlay.contains( artistId ) )
            if ( (now - artistsMostRecentPlay.value( artistId )) < rotSecs )
              rotationBlock = true;
          if ( !rotationBlock )
            { // TODO: consideration for streak allowance
              //       remove when related artists are on rotation block,
              //       reduce weight for related artists in recovery window
              qreal recovery = query.value( "recovery" ).toReal( &ok );
              if ( !ok ) recovery = DEF_RECOVERY_ART;
              qint64 recSecs = rotationValueToSeconds( recovery ); // same formula as rotation
              if ( artistsMostRecentPlay.contains( artistId ) )
                if ( (now - artistsMostRecentPlay.value( artistId )) < (rotSecs + recSecs) )
                  weight *= recoveryWeight( now - artistsMostRecentPlay.value( artistId ), rotSecs, recSecs );

              eligibleArtists.insert( artistId, weight );
              breathe();
            }
        }
    }
  // qDebug( "%d eligibleArtists", eligibleArtists.size() );
  return true;
}

/**
 * @brief MusicDirector::initEligibleTracks - based on the most recent play structure
 *   and current time, determine which tracks are eligible for play.
 *   TODO: consider the future - estimate what the list will look like when the track
 *     to be selected will actually be played.
 * @return true if successful
 */
bool MusicDirector::initEligibleTracks()
{ if ( !initEligibleArtists() )
    return false;
  eligibleTracks.clear();
  qint64 now = QDateTime::currentSecsSinceEpoch();
  QSqlQuery query(da->db);
  query.prepare( "SELECT * FROM tracks;" );
  if ( !query.exec() ) { qDebug( "ERROR: %s, %s", qPrintable( query.lastError().text() ), qPrintable( query.executedQuery() ) ); return false; }
  while ( query.next() ) // Start with a map of all tracks
    { bool ok = false;
      qint32 trackId = query.value( "trackId" ).toInt( &ok );
      if ( ok )
        { qreal restraint = query.value( "restraint" ).toReal( &ok );
          if ( !ok ) restraint = 0.0;
          qreal weight = pow(10.0,-restraint);
          qint32 artistId = query.value( "artistId" ).toInt( &ok );
          if ( ok )
            { if ( eligibleArtists.contains( artistId ) )
                { weight *= eligibleArtists.value( artistId );
                  qreal restraint = query.value( "restraint" ).toReal( &ok );
                  if ( !ok ) restraint = DEF_RESTRAINT;
                  qreal weight = pow(10.0,-restraint);

                  qreal rotation = query.value( "rotation" ).toReal( &ok );
                  if ( !ok ) rotation = DEF_ROTATION_TRK;
                  qint64 rotSecs = rotationValueToSeconds( rotation );
                  bool rotationBlock = false;
                  if ( tracksMostRecentPlay.contains( trackId ) )
                    if ( (now - tracksMostRecentPlay.value( trackId )) < rotSecs )
                      rotationBlock = true;
                  QMap<qint32,qreal> relTrk = de.relatedTracks( trackId );
                  foreach ( qint32 tid, relTrk )
                    if ( tracksMostRecentPlay.contains( tid ) )
                      if ( (now - tracksMostRecentPlay.value( tid )) < rotSecs )
                        rotationBlock = true;
                  if ( !rotationBlock )
                    { qreal recovery = query.value( "recovery" ).toReal( &ok );
                      if ( !ok ) recovery = DEF_RECOVERY_TRK;
                      qint64 recSecs = rotationValueToSeconds( recovery ); // same formula as rotation
                      if ( tracksMostRecentPlay.contains( trackId ) )
                        if ( (now - tracksMostRecentPlay.value( trackId )) < (rotSecs + recSecs) )
                          weight *= recoveryWeight( now - tracksMostRecentPlay.value( trackId ), rotSecs, recSecs );
                      foreach ( qint32 tid, relTrk )
                        if ( tracksMostRecentPlay.contains( tid ) )
                          if ( (now - tracksMostRecentPlay.value( tid )) < (rotSecs + recSecs * relTrk.value(tid) ) )
                            weight *= recoveryWeight( now - tracksMostRecentPlay.value( trackId ), rotSecs, recSecs * relTrk.value(tid) );
                      QString occasions = query.value( "occasions" ).toString();
                      if ( occasions.size() > 0 )
                        weight *= occasionWeight( occasions );
                      if ( weight > minWeightLimit )
                        eligibleTracks.insert( trackId, weight );
                      breathe();
                    }
                }
            }
        }
    }
  // qDebug( "%d eligibleTracks", eligibleTracks.size() );
  return true;
}

qreal MusicDirector::occasionWeight( QString occasions )
{  qreal weight = 1.0;
   QDate   date = QDateTime::currentDateTime().date();
  qint32  month = date.month();
  qint32    day = date.day();
  if ( occasions.contains( "[C]" ) ) // [C] is for Christmas
    { if ( month < 11 )
        weight *= 0.000001;
       else
        { qint32 daysToChristmas = ((month == 12) ? 25 : 55) - day;
          if        ( daysToChristmas == 0 ) weight *= 10.0;
            else if ( daysToChristmas  < 0 ) weight *= -1.0 /      (qreal)daysToChristmas;
            else if ( month == 12 )          weight *=  5.0 / sqrt((qreal)daysToChristmas); // December starts at 1.0 and rises to 5.0 by the 24th
            else weight *= pow( 25.0 / ((qreal)daysToChristmas), 3.0 );                     // November starts ~0.05 and ends at 1.0
        }
    }
  if ( occasions.contains( "[W]" ) ) // [W] is for Winter
    { if       ( month == 11 ) weight *= 0.5;
       else if ( month == 12 ) weight *= 2.0;
       else if ( month ==  1 ) weight *= 1.5;
       else if ( month ==  2 ) weight *= 1.0;
       else if ( month ==  3 ) weight *= 0.25;
       else                    weight *= 0.000001;
    }
  if ( occasions.contains( "[S]" ) ) // [S] is for Summer
    { if       ( month ==  5 ) weight *= 0.5;
       else if ( month ==  6 ) weight *= 2.0;
       else if ( month ==  7 ) weight *= 1.5;
       else if ( month ==  8 ) weight *= 1.0;
       else                    weight *= 0.2;
    }
  if ( occasions.contains( "[K]" ) ) // [K] is for Kids' Songs
    weight *= kidSongWeight;

  return weight;
}

/**
 * @brief MusicDirector::eligibleRadioCuts
 * @return Map with trackId keys, and (cutId, weight) values
 */
QMap<qint32,QPair<qint32,qreal> > MusicDirector::eligibleRadioCuts()
{ QMap<qint32,QPair<qint32,qreal> > cuts;
  if ( !initEligibleTracks() )
    return cuts;
  QSqlQuery query(da->db);
  query.prepare( "SELECT * FROM cuts WHERE cutType=:typ;" );
  query.bindValue( ":typ", "Radio" ); // Only select Radio cuts
  if ( !query.exec() ) { qDebug( "ERROR: %s, %s", qPrintable( query.lastError().text() ), qPrintable( query.executedQuery() ) ); return cuts; }
  while ( query.next() )
    {   bool      ok = false;
      qint32 trackId = query.value( "trackId" ).toInt(&ok);
      if ( ok )
        if ( eligibleTracks.contains( trackId ) ) // Only include cuts whose trackIds are eligible
          { qint32 cutId = query.value( "cutId" ).toInt(&ok);
            if ( ok )
              { qint32 startFrame = query.value( "startFrame" ).toInt( &ok );
                if ( ok )
                  { qint32 endFrame = query.value( "endFrame" ).toInt( &ok );
                    if ( ok )
                      { if ( endFrame > startFrame )
                          { if ((             startFrame  < (sampleRate * maxDepth ) ) &&
                                ( (endFrame - startFrame) < (sampleRate * maxLength) ) &&
                                ( (endFrame - startFrame) > (sampleRate * minLength) ))
                              { qreal midNframes = ((qreal)sampleRate) * 180.0; // 3 minutes, nominal
                                qreal cutLengthRatio = midNframes / ((qreal)(endFrame - startFrame));
                                if ( cutLengthRatio > 4.0 )
                                  cutLengthRatio = 4.0;  // Max bonus 2.0 at 45 seconds
                                // Longer cuts get lower weights, but sqrt mitigates this
                                qreal weight = eligibleTracks.value(trackId) * sqrt( cutLengthRatio );
                                if ( weight > minWeightLimit )
                                  cuts.insertMulti( trackId, QPair<qint32,qreal>(cutId,weight) );
                              }
                          }
                      }
                  }
              }
          }
      breathe();
    }
  return cuts;
}

/**
 * @brief MusicDirector::selectCuts
 * @param n - number of cuts to select
 */
bool MusicDirector::selectCuts( qint32 n )
{ if ( selectingCuts )
    return false;
  selectingCuts = true;
  if ( autoSelectStation )
    { selectStationByTime();
      autoSelectStation = true;
    }
  if ( po )
    if ( !po->paused )
      qDebug( "selecting %d cuts", n );
  QMap<qint32,QPair<qint32,qreal> > cuts = eligibleRadioCuts();
  if ( cuts.size() < n )
    { if ( po )
        if ( !po->paused )
          qDebug( "only %d elegible cuts, waiting.", cuts.size() );
      selectingCuts = false;
      return false;
    }
  qDebug( "eligible cuts size %d", cuts.size() );
  QList<qint32> seedTracks = getSeedTracks();

  // Reduce cuts size to ~exclPoolSz by removing "most unlike" tracks for each coreTrack
  QMap<qreal, qint32> tmp;  // Temporary sorted list of values to choose from
  if ( seedTracks.size() > 0 )
    if ( cuts.size() > (exclPoolSz + seedTracks.size()) )
      { qint32 nCutsToRemovePerCoreTrack = (cuts.size() - exclPoolSz) / seedTracks.size();
        // qDebug( "%d core tracks, removing %d cuts each", seedTracks.size(), nCutsToRemovePerCoreTrack );
        foreach ( qint32 coreTrack, seedTracks )
          { tmp.clear();
            AcousticBrainzValues ctv = AcousticBrainzValues( coreTrack );
            QList<qint32> tracks = cuts.keys();
            foreach( qint32 tid, tracks )
              { tmp.insertMulti( ctv.distSq( AcousticBrainzValues( tid ) ), tid );
                breathe();
              }
            QList<qint32> tValues = tmp.values().mid( tmp.values().size() - nCutsToRemovePerCoreTrack, nCutsToRemovePerCoreTrack );
            // qDebug( "cuts size %d before removing %d", cuts.size(), tValues.size() );
            foreach ( qint32 tid, tValues )
              cuts.remove( tid );
            // qDebug( "cuts size %d after removing %d (sum %d)", cuts.size(), tValues.size(), cuts.size() + tValues.size() );
          }
      }
  // qDebug( "cuts size %d (cutdown)", cuts.size() );

  // Get a list (lst) of the tracks "most like" each of the core tracks
  QMap<qreal, qint32> lst;
  QList<qint32> tracks = cuts.keys();
  // qDebug( "tracks size %d", tracks.size() );
  if ( seedTracks.size() < 1 )
    { QMapIterator<qint32,QPair<qint32,qreal> > it(cuts);
      while (it.hasNext())
        { it.next();
          lst.insert( -it.value().first, it.value().second );  // -it.value().first so the highest weights come first
        }
    }
   else
    { qint32 tracksPerCore = randPoolSz * 2 / seedTracks.size();
      QList<qint32>ct = seedTracks;
      foreach ( qint32 coreTrack, ct )
        { tmp.clear();
          AcousticBrainzValues ctv = AcousticBrainzValues( coreTrack );
          foreach( qint32 tid, tracks )
            { tmp.insertMulti( ctv.distSq( AcousticBrainzValues( tid ) ), tid );
              breathe();
            }
          // tmp.values() is a list of trackIDs, sorted by similarity to last track in the queue
          qint32 counter = tracksPerCore;
          QMapIterator<qreal, qint32> it(tmp);
          while (it.hasNext() && (counter-- > 0) )
            { it.next();
              lst.insert( -it.key(), it.value() ); // -it.key() so the highest weights come first
            }
        }
    }
  // qDebug( "lst size %d", lst.size() );

  // Now, extract the randPoolSz + n*5 first keys (highest weights) from lst into tmp
  qint32 counter = randPoolSz + n*5;
  QList<qint32> tlst;
  QMapIterator<qreal, qint32> it(lst);
  while (it.hasNext() && (counter-- > 0) )
    { it.next();
      tlst.append( it.value() );
    }
  suggestedTracks = tlst;

  tmp.clear();
  if ( po )
    { if ( po->cutQueue.size() > 0 )
        { qint32 lastTrack = de.cutTrackId( po->cutQueue.last()->getId() );
          AcousticBrainzValues npv = AcousticBrainzValues( lastTrack );
          foreach( qint32 tid, lst ) // Re-sort the list against the last song in the queue
            { tmp.insertMulti( npv.distSq( AcousticBrainzValues( tid ) ), tid );
              breathe();
            }
        }
    }

  tlst.clear();
  if ( tmp.size() > 0 )
    tlst = tmp.values(); // list of trackIDs, sorted by similarity to last track in the queue
   else
    { tlst = tracks;     // No tracks in the queue, shuffle the selected tracks list randomly
      std::random_shuffle( tlst.begin(), tlst.end() );
    }


  for ( qint32 i = 0; i < n; i++ )
    { QList<qint32> trkLst = tlst.mid( 0, randPoolSz );
      qreal simWt = 1.0; // Similarity weight modifier, decreases to ~1.7% after 100 iterations * 0.96
      QMultiHash<qint32,qreal> weightedTracks;
      foreach ( qint32 tid, trkLst )
        { weightedTracks.insert( tid, cuts.value(tid).second * simWt );
          // qDebug( "%f %f %s", cuts.value(tid).second * simWt, cuts.value(tid).second, qPrintable( de.trackDescription(tid) ) );
          simWt *= 0.96;
        }
      qint32 trackN = selectTrack( weightedTracks );
      emit cutSelected( cuts.value(trackN).first );
      tlst.removeAll( trackN );
      breathe();
    }
  qDebug( "selectCuts(%d) done", n );
  selectingCuts = false;
  return true;
}

/**
 * @brief MusicDirector::getSeedTracks
 * @return a subset list of coreTracks, at most maxSeeds long
 */
QList<qint32>MusicDirector::getSeedTracks()
{ QList<qint32>trackList = coreTracks;
  if ( trackList.size() <= maxSeeds )
    return trackList;
  QMultiMap<qint64,qint32>playtimeTrackMap;
  foreach ( qint32 trackId, trackList )
    playtimeTrackMap.insert( de.trackMostRecentPlay( trackId ), trackId );
//   foreach ( qint32 track, trackList )
//     qDebug( "coreTrack: %d last played %lld", track, playtimeTrackMap.key(track) );
  // First, down-select to a single track from each artist
  QMultiHash<qint32,qint32>atHash;
  foreach ( qint32 trackId, trackList )
    { qint32 artistId = de.trackArtistId( trackId );
      if ( artistId >= 0 )
        atHash.insert( artistId, trackId );
    }
  QList<qint32> artistList = atHash.uniqueKeys();
  if ( artistList.size() < trackList.size() )
    foreach ( qint32 artistId, artistList )
      { QList<qint32> aTrackIds = atHash.values( artistId );
        if ( aTrackIds.size() > 1 )
          { qint64 leastRecentPlay = QDateTime::currentSecsSinceEpoch();
            foreach ( qint32 aTrackId, aTrackIds )
              if ( playtimeTrackMap.key( aTrackId, QDateTime::currentSecsSinceEpoch() ) < leastRecentPlay )
                leastRecentPlay = playtimeTrackMap.key( aTrackId, QDateTime::currentSecsSinceEpoch() );
            foreach ( qint32 aTrackId, aTrackIds )
              { if ( playtimeTrackMap.key( aTrackId ) > leastRecentPlay )
                  { trackList.removeAll( aTrackId );
                    playtimeTrackMap.remove( leastRecentPlay );
                    if ( trackList.size() <= maxSeeds )
                      return trackList;
                  }
              }
          } // if ( aTrackIds.size() > 1 )
      } // foreach ( qint32 artistId, artistList )
  // Now, down-select to the least recently played tracks
  // trackList = playtimeTrackMap.values().mid( 0, maxSeeds );
  // qDebug( "maxSeeds %d", maxSeeds );
  // foreach ( qint32 track, trackList )
  //   qDebug( "seedTrack: %d last played %lld", track, playtimeTrackMap.key(track) );
  return playtimeTrackMap.values().mid( 0, maxSeeds );
}

/**
 * @brief MusicDirector::selectTrack
 * @param weightedTracks - each hash entry has an id and a weight
 * @return randomly selected id using weights as relative probability of selection
 */
qint32 MusicDirector::selectTrack( QMultiHash<qint32,qreal> weightedTracks )
{ if ( weightedTracks.size() < 1 )
    { qDebug( "WARNING: selectTrack() received an empty hash table." );
      return 0;
    }
  breathe();
  qreal weightSum = 0.0;
  QHashIterator<qint32, qreal> i(weightedTracks);
  while (i.hasNext())
    { i.next();
      weightSum += i.value();
      // qDebug( "%lf %lf", i.value(), weightSum );
    }
  qreal target = rng.bounded(weightSum);
  // qDebug( "target %lf", target );
  breathe();

  weightSum = 0.0;
  i.toFront();
  while (i.hasNext())
    { i.next();
      weightSum += i.value();
      // qDebug( "%lf %lf %lf", i.value(), weightSum, target );
      if ( weightSum >= target )
        return i.key();
    } 
  qDebug( "WARNING: rng weighted search ran off the end, should never happen." );
  i.toFront();
  i.next();
  return i.key();
}

//////////////////////////////////////////////////////////////////////////

AcousticBrainzValues::AcousticBrainzValues(QObject *parent)
     : QObject( parent ),
        danceable( 0.5 ),
           female( 0.5 ),
         acoustic( 0.5 ),
       aggressive( 0.5 ),
            happy( 0.5 ),
            party( 0.5 ),
          relaxed( 0.5 ),
              sad( 0.5 ),
           bright( 0.5 ),
            tonal( 0.5 ),
     instrumental( 0.5 )
{}

AcousticBrainzValues::AcousticBrainzValues( const AcousticBrainzValues &v2 )
     : QObject( v2.parent() ),
        danceable( v2.danceable    ),
           female( v2.female       ),
         acoustic( v2.acoustic     ),
       aggressive( v2.aggressive   ),
            happy( v2.happy        ),
            party( v2.party        ),
          relaxed( v2.relaxed      ),
              sad( v2.sad          ),
           bright( v2.bright       ),
            tonal( v2.tonal        ),
     instrumental( v2.instrumental )
{}

AcousticBrainzValues::AcousticBrainzValues( qint32 trackId, QObject *parent )
    : QObject( parent ),
       danceable( 0.5 ),
          female( 0.5 ),
        acoustic( 0.5 ),
      aggressive( 0.5 ),
           happy( 0.5 ),
           party( 0.5 ),
         relaxed( 0.5 ),
             sad( 0.5 ),
          bright( 0.5 ),
           tonal( 0.5 ),
    instrumental( 0.5 )
{ QSqlQuery query(da->db);
  query.prepare( "SELECT * FROM tracks WHERE trackId=:id;" );
  query.bindValue( ":id", trackId );
  if ( !query.exec() ) { qDebug( "exec failed %s", query.lastError().text().toUtf8().data() ); } else
    if ( !query.first() ) { qDebug( "abv %d not found %s", trackId, query.lastQuery().toUtf8().data() ); } else
      { bool ok = false;
        qreal v;
        v = query.value( "abDanceable"    ).toReal( &ok ); if ( ok )    danceable = v;
        v = query.value( "abFemale"       ).toReal( &ok ); if ( ok )       female = v;
        v = query.value( "abAcoustic"     ).toReal( &ok ); if ( ok )     acoustic = v;
        v = query.value( "abAggressive"   ).toReal( &ok ); if ( ok )   aggressive = v;
        v = query.value( "abHappy"        ).toReal( &ok ); if ( ok )        happy = v;
        v = query.value( "abParty"        ).toReal( &ok ); if ( ok )        party = v;
        v = query.value( "abRelaxed"      ).toReal( &ok ); if ( ok )      relaxed = v;
        v = query.value( "abSad"          ).toReal( &ok ); if ( ok )          sad = v;
        v = query.value( "abBright"       ).toReal( &ok ); if ( ok )       bright = v;
        v = query.value( "abTonal"        ).toReal( &ok ); if ( ok )        tonal = v;
        v = query.value( "abInstrumental" ).toReal( &ok ); if ( ok ) instrumental = v;
      }
}

void AcousticBrainzValues::debugShow()
{
  qDebug( "   danceable %f\n"
          "      female %f\n"
          "    acoustic %f\n"
          "  aggressive %f\n"
          "       happy %f\n"
          "       party %f\n"
          "     relaxed %f\n"
          "         sad %f\n"
          "      bright %f\n"
          "       tonal %f\n"
          "instrumental %f\n",
              danceable,
                 female,
               acoustic,
             aggressive,
                  happy,
                  party,
                relaxed,
                    sad,
                 bright,
                  tonal,
           instrumental );
}

qreal AcousticBrainzValues::distSq( const AcousticBrainzValues &v2 )
{ return (    danceable - v2.danceable    ) * (    danceable - v2.danceable    ) +
         (       female - v2.female       ) * (       female - v2.female       ) +
         (     acoustic - v2.acoustic     ) * (     acoustic - v2.acoustic     ) +
         (   aggressive - v2.aggressive   ) * (   aggressive - v2.aggressive   ) +
         (        happy - v2.happy        ) * (        happy - v2.happy        ) +
         (        party - v2.party        ) * (        party - v2.party        ) +
         (      relaxed - v2.relaxed      ) * (      relaxed - v2.relaxed      ) +
         (          sad - v2.sad          ) * (          sad - v2.sad          ) +
         (       bright - v2.bright       ) * (       bright - v2.bright       ) +
         (        tonal - v2.tonal        ) * (        tonal - v2.tonal        ) +
         ( instrumental - v2.instrumental ) * ( instrumental - v2.instrumental );
}
